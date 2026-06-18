#!/usr/bin/env python3
"""Bidirectional Chrome and Safari bookmark sync for macOS.

Default mode is a dry run. Use --apply to write bookmark files.
The sync is conservative: it only adds missing URLs and never deletes,
renames, or moves existing bookmarks.
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import os
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterator, Optional
from urllib.parse import unquote_plus, urlsplit, urlunsplit


DEFAULT_CHROME_ROOT = Path(
    "~/Library/Application Support/Google/Chrome"
).expanduser()
DEFAULT_SAFARI_BOOKMARKS = Path("~/Library/Safari/Bookmarks.plist").expanduser()
DEFAULT_SAFARI_TARGET_FOLDER = "Imported from Google Chrome"
DEFAULT_CHROME_TARGET_FOLDER = "Imported from Safari"
TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
    "msclkid",
    "mc_eid",
    "mc_cid",
    "igshid",
    "_ga",
    "_gl",
    "yclid",
    "dclid",
    "wbraid",
    "gbraid",
    "ref_src",
    "ref_url",
}
DEFAULT_BACKUP_RETENTION = 10
DEFAULT_LOCK_TIMEOUT = 10.0


@dataclass(frozen=True)
class Bookmark:
    title: str
    url: str
    path: tuple[str, ...]
    source: Path


@dataclass(frozen=True)
class SyncPlan:
    chrome_to_safari: list[Bookmark]
    safari_to_chrome: list[Bookmark]
    chrome_duplicates: int
    safari_duplicates: int
    chrome_target: Path
    active_bookmarks_skipped: int = 0


@dataclass(frozen=True)
class FileFingerprint:
    size: int
    mtime_ns: int
    digest: str


class ConcurrentModificationError(RuntimeError):
    pass


class SyncLockError(RuntimeError):
    pass


class BrowserRunningError(RuntimeError):
    pass


def _filter_tracking_query(query: str) -> str:
    kept: list[str] = []
    for item in query.split("&"):
        key = item.partition("=")[0]
        if unquote_plus(key).lower() not in TRACKING_PARAMS:
            kept.append(item)
    return "&".join(kept)


def normalize_url(url: str, policy: str = "conservative") -> str:
    """Build a comparison key without changing the URL that will be written."""
    raw = url.strip()
    try:
        parsed = urlsplit(raw)
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc
        if parsed.hostname:
            userinfo, separator, _ = netloc.rpartition("@")
            host = parsed.hostname.lower()
            if ":" in host:
                host = f"[{host}]"
            port = parsed.port
            if port is not None and not (
                (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
            ):
                host = f"{host}:{port}"
            netloc = f"{userinfo}{separator}{host}" if separator else host
        path = parsed.path
        if netloc and not path:
            path = "/"
        query = parsed.query
        if policy == "tracking":
            query = _filter_tracking_query(query)
        return urlunsplit((scheme, netloc, path, query, parsed.fragment))
    except (TypeError, ValueError):
        return raw


def is_active_bookmark(url: str) -> bool:
    try:
        return urlsplit(url.strip()).scheme.lower() in {"javascript", "data"}
    except (TypeError, ValueError):
        return False


def chrome_timestamp() -> str:
    epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return str(int((now - epoch).total_seconds() * 1_000_000))


def fingerprint_bytes(content: bytes, stat_result: os.stat_result) -> FileFingerprint:
    return FileFingerprint(
        size=len(content),
        mtime_ns=stat_result.st_mtime_ns,
        digest=hashlib.sha256(content).hexdigest(),
    )


def read_stable_bytes(path: Path, attempts: int = 3) -> tuple[bytes, FileFingerprint]:
    last_error: Optional[ConcurrentModificationError] = None
    for _ in range(attempts):
        before = path.stat()
        content = path.read_bytes()
        after = path.stat()
        if (
            before.st_dev == after.st_dev
            and before.st_ino == after.st_ino
            and before.st_size == after.st_size
            and before.st_mtime_ns == after.st_mtime_ns
            and len(content) == after.st_size
        ):
            return content, fingerprint_bytes(content, after)
        last_error = ConcurrentModificationError(f"File changed while being read: {path}")
    raise last_error or ConcurrentModificationError(f"Unable to read a stable snapshot: {path}")


def current_fingerprint(path: Path) -> FileFingerprint:
    content, fingerprint = read_stable_bytes(path)
    return fingerprint


def assert_fingerprint(path: Path, expected: FileFingerprint) -> None:
    if current_fingerprint(path) != expected:
        raise ConcurrentModificationError(
            f"Bookmark file changed after planning; no write was performed: {path}"
        )


def load_json_snapshot(path: Path) -> tuple[dict, FileFingerprint]:
    content, fingerprint = read_stable_bytes(path)
    data = json.loads(content.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Chrome bookmarks root is not an object: {path}")
    if "roots" in data and not isinstance(data["roots"], dict):
        raise ValueError(f"Chrome bookmarks roots are not an object: {path}")
    return data, fingerprint


def load_plist_snapshot(path: Path) -> tuple[dict, FileFingerprint]:
    content, fingerprint = read_stable_bytes(path)
    data = plistlib.loads(content)
    if not isinstance(data, dict):
        raise ValueError(f"Safari bookmarks root is not a dictionary: {path}")
    if "Children" in data and not isinstance(data["Children"], list):
        raise ValueError(f"Safari bookmark Children is not an array: {path}")
    return data, fingerprint


def load_json(path: Path) -> dict:
    return load_json_snapshot(path)[0]


def load_plist(path: Path) -> dict:
    return load_plist_snapshot(path)[0]


def backup_path(path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return path.with_name(f"{path.name}.bak-{timestamp}")


def validate_write_target(path: Path) -> os.stat_result:
    if path.is_symlink():
        raise ValueError(f"Refusing to replace a symbolic link: {path}")
    result = path.stat()
    if not stat.S_ISREG(result.st_mode):
        raise ValueError(f"Bookmark target is not a regular file: {path}")
    return result


def fsync_directory(path: Path) -> None:
    directory_fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def prune_backups(path: Path, retention: int) -> None:
    if retention < 1:
        return
    backups = sorted(
        path.parent.glob(f"{path.name}.bak-*"),
        key=lambda item: item.name,
        reverse=True,
    )
    for old_backup in backups[retention:]:
        if old_backup.is_file() or old_backup.is_symlink():
            old_backup.unlink()


def write_bytes_atomic(
    content: bytes,
    path: Path,
    expected: FileFingerprint,
    retention: int,
) -> Path:
    target_stat = validate_write_target(path)
    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile("wb", delete=False, dir=str(path.parent)) as tmp:
            tmp.write(content)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = Path(tmp.name)
        os.chmod(tmp_path, stat.S_IMODE(target_stat.st_mode))
        assert_fingerprint(path, expected)
        backup = backup_path(path)
        shutil.copy2(path, backup)
        with backup.open("rb") as backup_handle:
            os.fsync(backup_handle.fileno())
        fsync_directory(path.parent)
        os.replace(tmp_path, path)
        tmp_path = None
        fsync_directory(path.parent)
        prune_backups(path, retention)
        return backup
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def save_json_atomic(
    data: dict,
    path: Path,
    expected: Optional[FileFingerprint] = None,
    retention: int = DEFAULT_BACKUP_RETENTION,
) -> Path:
    refresh_chrome_checksum(data)
    content = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    parsed = json.loads(content.decode("utf-8"))
    if parsed.get("checksum") != compute_chrome_checksum(parsed):
        raise ValueError("Generated Chrome bookmark checksum failed verification")
    return write_bytes_atomic(
        content,
        path,
        expected or current_fingerprint(path),
        retention,
    )


def save_plist_atomic(
    data: dict,
    path: Path,
    expected: Optional[FileFingerprint] = None,
    retention: int = DEFAULT_BACKUP_RETENTION,
) -> Path:
    content = plistlib.dumps(data, fmt=plistlib.FMT_BINARY, sort_keys=False)
    if not isinstance(plistlib.loads(content), dict):
        raise ValueError("Generated Safari bookmark plist failed verification")
    return write_bytes_atomic(
        content,
        path,
        expected or current_fingerprint(path),
        retention,
    )


def update_chrome_checksum(md5: "hashlib._Hash", text: str, utf16: bool = False) -> None:
    if utf16:
        md5.update(text.encode("utf-16-le", errors="surrogatepass"))
    else:
        md5.update(text.encode("utf-8"))


def update_chrome_checksum_for_node(md5: "hashlib._Hash", node: dict) -> None:
    node_id = str(node.get("id") or "")
    title = str(node.get("name") or "")
    node_type = str(node.get("type") or "")
    update_chrome_checksum(md5, node_id)
    update_chrome_checksum(md5, title, utf16=True)
    update_chrome_checksum(md5, node_type)
    if node_type == "url":
        update_chrome_checksum(md5, str(node.get("url") or ""))
        return
    for child in node.get("children") or []:
        if isinstance(child, dict):
            update_chrome_checksum_for_node(md5, child)


def compute_chrome_checksum(data: dict) -> str:
    md5 = hashlib.md5()
    roots = data.get("roots") or {}
    for key in ("bookmark_bar", "other", "synced"):
        node = roots.get(key)
        if isinstance(node, dict):
            update_chrome_checksum_for_node(md5, node)
    checksum = md5.hexdigest()
    return checksum


def refresh_chrome_checksum(data: dict) -> str:
    checksum = compute_chrome_checksum(data)
    data["checksum"] = checksum
    return checksum


def discover_chrome_bookmarks(chrome_root: Path) -> list[Path]:
    if not chrome_root.is_dir():
        return []
    candidates = [
        path
        for path in chrome_root.glob("*/Bookmarks")
        if path.is_file() and not path.is_symlink() and not path.parent.is_symlink()
    ]
    return sorted(
        candidates,
        key=lambda path: (
            path.parent.name != "Default",
            path.parent.name.casefold(),
        ),
    )


def chrome_root_label(key: str, node: dict) -> str:
    if key == "bookmark_bar":
        return "Bookmarks Bar"
    if key == "other":
        return "Other Bookmarks"
    if key == "synced":
        return "Mobile Bookmarks"
    return node.get("name") or key


def safari_root_label(title: str) -> str:
    if title == "BookmarksBar":
        return "Safari Bookmarks Bar"
    if title == "BookmarksMenu":
        return "Safari Bookmarks Menu"
    if title == "com.apple.ReadingList":
        return "Safari Reading List"
    return title or "Safari"


def walk_chrome_node(
    node: dict,
    path: tuple[str, ...],
    source: Path,
    out: list[Bookmark],
    include_active: bool = False,
    stats: Optional[dict[str, int]] = None,
) -> None:
    node_type = node.get("type")
    if node_type == "url" and node.get("url"):
        url = str(node["url"]).strip()
        if url.startswith(("chrome://", "chrome-extension://", "about:")):
            return
        if is_active_bookmark(url) and not include_active:
            if stats is not None:
                stats["active_skipped"] = stats.get("active_skipped", 0) + 1
            return
        title = str(node.get("name") or url).strip() or url
        out.append(Bookmark(title=title, url=url, path=path, source=source))
        return

    children = node.get("children") or []
    next_path = path
    name = str(node.get("name") or "").strip()
    if node_type == "folder" and name:
        next_path = path + (name,)
    for child in children:
        if isinstance(child, dict):
            walk_chrome_node(child, next_path, source, out, include_active, stats)


def read_chrome_bookmarks(
    paths: list[Path],
    include_active: bool = False,
    stats: Optional[dict[str, int]] = None,
) -> list[Bookmark]:
    snapshots = {path: load_json(path) for path in paths}
    return read_chrome_bookmarks_from_data(snapshots, include_active, stats)


def read_chrome_bookmarks_from_data(
    snapshots: dict[Path, dict],
    include_active: bool = False,
    stats: Optional[dict[str, int]] = None,
) -> list[Bookmark]:
    bookmarks: list[Bookmark] = []
    multiple_profiles = len(snapshots) > 1
    for path, data in snapshots.items():
        roots = data.get("roots") or {}
        for key, node in roots.items():
            if not isinstance(node, dict):
                continue
            root_path: tuple[str, ...] = ()
            if multiple_profiles:
                root_path += (f"Chrome {path.parent.name}",)
            root_path += (chrome_root_label(key, node),)
            for child in node.get("children") or []:
                if isinstance(child, dict):
                    walk_chrome_node(
                        child,
                        root_path,
                        path,
                        bookmarks,
                        include_active,
                        stats,
                    )
    return bookmarks


def walk_safari_node(
    node: object,
    path: tuple[str, ...],
    source: Path,
    out: list[Bookmark],
    include_active: bool = False,
    stats: Optional[dict[str, int]] = None,
) -> None:
    if not isinstance(node, dict):
        return
    url = node.get("URLString")
    if isinstance(url, str) and url.strip():
        if is_active_bookmark(url) and not include_active:
            if stats is not None:
                stats["active_skipped"] = stats.get("active_skipped", 0) + 1
            return
        uri_dictionary = node.get("URIDictionary")
        title = uri_dictionary.get("title") if isinstance(uri_dictionary, dict) else None
        title = title or url
        out.append(Bookmark(str(title).strip() or url, url.strip(), path, source))
        return

    title = str(node.get("Title") or "").strip()
    next_path = path
    if title:
        next_path = path + (safari_root_label(title),)
    for child in node.get("Children") or []:
        walk_safari_node(child, next_path, source, out, include_active, stats)


def read_safari_bookmarks(
    data: dict,
    source: Path,
    include_active: bool = False,
    stats: Optional[dict[str, int]] = None,
) -> list[Bookmark]:
    bookmarks: list[Bookmark] = []
    for child in data.get("Children") or []:
        walk_safari_node(child, (), source, bookmarks, include_active, stats)
    return bookmarks


def find_child_folder(parent: dict, title: str) -> dict | None:
    for child in parent.get("Children") or []:
        if (
            isinstance(child, dict)
            and child.get("WebBookmarkType") == "WebBookmarkTypeList"
            and child.get("Title") == title
        ):
            return child
    return None


def new_safari_folder(title: str) -> dict:
    return {
        "Children": [],
        "Title": title,
        "WebBookmarkType": "WebBookmarkTypeList",
        "WebBookmarkUUID": str(uuid.uuid4()).upper(),
        "dateAdded": datetime.utcnow(),
    }


def new_safari_leaf(bookmark: Bookmark) -> dict:
    return {
        "URIDictionary": {"title": bookmark.title},
        "URLString": bookmark.url,
        "WebBookmarkType": "WebBookmarkTypeLeaf",
        "WebBookmarkUUID": str(uuid.uuid4()).upper(),
        "dateAdded": datetime.utcnow(),
    }


def ensure_safari_folder(
    parent: dict,
    title: str,
    index: Optional[dict[int, dict[str, dict]]] = None,
) -> dict:
    parent.setdefault("Children", [])
    folders: Optional[dict[str, dict]] = None
    if index is not None:
        parent_key = id(parent)
        folders = index.get(parent_key)
        if folders is None:
            folders = {}
            for child in parent.get("Children") or []:
                if (
                    isinstance(child, dict)
                    and child.get("WebBookmarkType") == "WebBookmarkTypeList"
                    and isinstance(child.get("Title"), str)
                ):
                    folders.setdefault(child["Title"], child)
            index[parent_key] = folders
        existing = folders.get(title)
    else:
        existing = find_child_folder(parent, title)
    if existing is not None:
        return existing
    folder = new_safari_folder(title)
    parent["Children"].append(folder)
    if folders is not None:
        folders[title] = folder
    return folder


def add_bookmark_under_target(
    root: dict,
    target_name: str,
    bookmark: Bookmark,
    index: Optional[dict[int, dict[str, dict]]] = None,
) -> None:
    target = ensure_safari_folder(root, target_name, index)
    folder = target
    for part in bookmark.path:
        folder = ensure_safari_folder(folder, part, index)
    folder.setdefault("Children", []).append(new_safari_leaf(bookmark))


def deduplicate_bookmarks(
    bookmarks: list[Bookmark],
    policy: str = "conservative",
) -> tuple[list[Bookmark], int]:
    seen: set[str] = set()
    unique: list[Bookmark] = []
    duplicate_count = 0
    for bookmark in bookmarks:
        normalized = normalize_url(bookmark.url, policy)
        if normalized in seen:
            duplicate_count += 1
            continue
        seen.add(normalized)
        unique.append(bookmark)
    return unique, duplicate_count


def plan_sync(
    chrome_bookmarks: list[Bookmark],
    safari_bookmarks: list[Bookmark],
    chrome_target: Path,
    mode: str,
    dedup_policy: str = "conservative",
    active_bookmarks_skipped: int = 0,
) -> SyncPlan:
    unique_chrome, chrome_duplicate_count = deduplicate_bookmarks(
        chrome_bookmarks, dedup_policy
    )
    unique_safari, safari_duplicate_count = deduplicate_bookmarks(
        safari_bookmarks, dedup_policy
    )
    chrome_items = [
        (normalize_url(bookmark.url, dedup_policy), bookmark)
        for bookmark in unique_chrome
    ]
    safari_items = [
        (normalize_url(bookmark.url, dedup_policy), bookmark)
        for bookmark in unique_safari
    ]
    chrome_urls = {key for key, _ in chrome_items}
    safari_urls = {key for key, _ in safari_items}

    chrome_to_safari: list[Bookmark] = []
    safari_to_chrome: list[Bookmark] = []
    if mode in ("both", "chrome-to-safari"):
        chrome_to_safari = [
            bookmark for key, bookmark in chrome_items if key not in safari_urls
        ]
    if mode in ("both", "safari-to-chrome"):
        safari_to_chrome = [
            bookmark for key, bookmark in safari_items if key not in chrome_urls
        ]

    return SyncPlan(
        chrome_to_safari=chrome_to_safari,
        safari_to_chrome=safari_to_chrome,
        chrome_duplicates=chrome_duplicate_count,
        safari_duplicates=safari_duplicate_count,
        chrome_target=chrome_target,
        active_bookmarks_skipped=active_bookmarks_skipped,
    )


def chrome_max_id(node: object) -> int:
    if not isinstance(node, dict):
        return 0
    max_id = 0
    node_id = str(node.get("id") or "")
    if node_id.isdigit():
        max_id = int(node_id)
    for child in node.get("children") or []:
        max_id = max(max_id, chrome_max_id(child))
    return max_id


def next_chrome_id(counter: list[int]) -> str:
    counter[0] += 1
    return str(counter[0])


def find_chrome_folder(parent: dict, name: str) -> dict | None:
    for child in parent.get("children") or []:
        if isinstance(child, dict) and child.get("type") == "folder" and child.get("name") == name:
            return child
    return None


def new_chrome_folder(name: str, counter: list[int]) -> dict:
    now = chrome_timestamp()
    return {
        "children": [],
        "date_added": now,
        "date_last_used": "0",
        "date_modified": now,
        "guid": str(uuid.uuid4()),
        "id": next_chrome_id(counter),
        "name": name,
        "type": "folder",
    }


def new_chrome_leaf(bookmark: Bookmark, counter: list[int]) -> dict:
    return {
        "date_added": chrome_timestamp(),
        "date_last_used": "0",
        "guid": str(uuid.uuid4()),
        "id": next_chrome_id(counter),
        "name": bookmark.title,
        "type": "url",
        "url": bookmark.url,
    }


def ensure_chrome_root(data: dict, key: str, counter: list[int]) -> dict:
    roots = data.setdefault("roots", {})
    root = roots.get(key)
    if not isinstance(root, dict):
        root = {
            "children": [],
            "date_added": chrome_timestamp(),
            "date_last_used": "0",
            "date_modified": chrome_timestamp(),
            "guid": str(uuid.uuid4()),
            "id": next_chrome_id(counter),
            "name": "Other Bookmarks",
            "type": "folder",
        }
        roots[key] = root
    root.setdefault("children", [])
    return root


def ensure_chrome_folder(
    parent: dict,
    name: str,
    counter: list[int],
    index: Optional[dict[int, dict[str, dict]]] = None,
) -> dict:
    parent.setdefault("children", [])
    folders: Optional[dict[str, dict]] = None
    if index is not None:
        parent_key = id(parent)
        folders = index.get(parent_key)
        if folders is None:
            folders = {}
            for child in parent.get("children") or []:
                if (
                    isinstance(child, dict)
                    and child.get("type") == "folder"
                    and isinstance(child.get("name"), str)
                ):
                    folders.setdefault(child["name"], child)
            index[parent_key] = folders
        existing = folders.get(name)
    else:
        existing = find_chrome_folder(parent, name)
    if existing is not None:
        return existing
    folder = new_chrome_folder(name, counter)
    parent["children"].append(folder)
    if folders is not None:
        folders[name] = folder
    return folder


def add_safari_bookmark_to_chrome(
    chrome_data: dict,
    target_folder: str,
    bookmark: Bookmark,
    counter: list[int],
    index: Optional[dict[int, dict[str, dict]]] = None,
) -> None:
    root = ensure_chrome_root(chrome_data, "other", counter)
    changed_folders = [root]
    folder = ensure_chrome_folder(root, target_folder, counter, index)
    changed_folders.append(folder)
    for part in bookmark.path:
        folder = ensure_chrome_folder(folder, part, counter, index)
        changed_folders.append(folder)
    folder.setdefault("children", []).append(new_chrome_leaf(bookmark, counter))
    modified = chrome_timestamp()
    for changed_folder in changed_folders:
        changed_folder["date_modified"] = modified


def format_preview(title: str, bookmarks: list[Bookmark], preview_limit: int) -> list[str]:
    lines = [title]
    if preview_limit <= 0:
        lines.append("- preview hidden")
        return lines
    preview = bookmarks[:preview_limit]
    if not preview:
        lines.append("- none")
        return lines
    for bookmark in preview:
        folder = " / ".join(bookmark.path) if bookmark.path else "(root)"
        lines.append(f"- [{folder}] {bookmark.title} <{bookmark.url}>")
    if len(bookmarks) > len(preview):
        lines.append(f"... {len(bookmarks) - len(preview)} more")
    return lines


def format_plan(
    plan: SyncPlan,
    apply: bool,
    mode: str,
    preview_limit: int,
    dedup_policy: str,
) -> str:
    lines = [
        f"Mode: {'apply' if apply else 'dry-run'}",
        f"Sync direction: {mode}",
        f"Deduplication policy: {dedup_policy}",
        f"Chrome duplicates skipped: {plan.chrome_duplicates}",
        f"Safari duplicates skipped: {plan.safari_duplicates}",
        f"Active bookmarks skipped: {plan.active_bookmarks_skipped}",
        f"New bookmarks to add to Safari: {len(plan.chrome_to_safari)}",
        f"New bookmarks to add to Chrome: {len(plan.safari_to_chrome)}",
        f"Chrome write target: {plan.chrome_target}",
        "",
    ]
    lines.extend(format_preview("Chrome -> Safari preview:", plan.chrome_to_safari, preview_limit))
    lines.append("")
    lines.extend(format_preview("Safari -> Chrome preview:", plan.safari_to_chrome, preview_limit))
    return "\n".join(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bidirectionally sync Google Chrome and Safari bookmarks with URL-based deduplication."
    )
    parser.add_argument(
        "--include-active-bookmarks",
        action="store_true",
        help="Include javascript: and data: bookmarks. They are skipped by default.",
    )
    parser.add_argument(
        "--dedup-policy",
        choices=("conservative", "tracking"),
        default="conservative",
        help=(
            "URL comparison policy. 'conservative' preserves hosts, query parameters, "
            "and fragments; 'tracking' also ignores known ad-tracking parameters. "
            "Default: conservative."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=("both", "chrome-to-safari", "safari-to-chrome"),
        default="both",
        help="Sync direction. Default: both.",
    )
    parser.add_argument(
        "--chrome-bookmarks",
        action="append",
        type=Path,
        help="Path to a Chrome Bookmarks JSON file. Can be used multiple times.",
    )
    parser.add_argument(
        "--chrome-root",
        type=Path,
        default=DEFAULT_CHROME_ROOT,
        help=f"Chrome user data root. Default: {DEFAULT_CHROME_ROOT}",
    )
    parser.add_argument(
        "--safari-bookmarks",
        type=Path,
        default=DEFAULT_SAFARI_BOOKMARKS,
        help=f"Safari Bookmarks.plist path. Default: {DEFAULT_SAFARI_BOOKMARKS}",
    )
    parser.add_argument(
        "--safari-target-folder",
        "--target-folder",
        default=DEFAULT_SAFARI_TARGET_FOLDER,
        help=f"Top-level Safari folder for Chrome imports. Default: {DEFAULT_SAFARI_TARGET_FOLDER}",
    )
    parser.add_argument(
        "--chrome-target-folder",
        default=DEFAULT_CHROME_TARGET_FOLDER,
        help=f"Chrome Other Bookmarks folder for Safari imports. Default: {DEFAULT_CHROME_TARGET_FOLDER}",
    )
    parser.add_argument(
        "--backup-retention",
        type=int,
        default=DEFAULT_BACKUP_RETENTION,
        help=f"Maximum backups retained per bookmark file. Default: {DEFAULT_BACKUP_RETENTION}.",
    )
    parser.add_argument(
        "--lock-timeout",
        type=float,
        default=DEFAULT_LOCK_TIMEOUT,
        help=f"Seconds to wait for another sync process. Default: {DEFAULT_LOCK_TIMEOUT:g}.",
    )
    parser.add_argument(
        "--lock-file",
        type=Path,
        help="Override the synchronization lock file path.",
    )
    parser.add_argument(
        "--allow-running-browsers",
        action="store_true",
        help=(
            "Allow writes while the destination browser is running. This can be "
            "overwritten by the browser's in-memory bookmark model."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes. Without this flag the command is a dry run.",
    )
    parser.add_argument(
        "--preview-limit",
        type=int,
        default=0,
        help="Maximum number of bookmark URLs to print per direction. Default: 0 (hidden).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress normal output. Errors are still written to stderr.",
    )
    return parser.parse_args(argv)


def resolve_paths(args: argparse.Namespace) -> tuple[list[Path], Path, Path]:
    chrome_paths = [p.expanduser() for p in args.chrome_bookmarks or []]
    if not chrome_paths:
        chrome_paths = discover_chrome_bookmarks(args.chrome_root.expanduser())
    if not chrome_paths:
        raise FileNotFoundError(f"No Chrome Bookmarks files found under {args.chrome_root.expanduser()}")
    missing = [str(path) for path in chrome_paths if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing Chrome Bookmarks file(s): " + ", ".join(missing))

    safari_path = args.safari_bookmarks.expanduser()
    if not safari_path.exists():
        raise FileNotFoundError(f"Safari bookmarks file not found: {safari_path}")

    return chrome_paths, chrome_paths[0], safari_path


@contextmanager
def acquire_sync_lock(path: Path, timeout: float) -> Iterator[None]:
    if timeout < 0:
        raise ValueError("--lock-timeout must be zero or greater")
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(str(path), flags, 0o600)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ValueError(f"Refusing to use a symbolic-link lock file: {path}") from exc
        raise
    lock_stat = os.fstat(descriptor)
    if not stat.S_ISREG(lock_stat.st_mode):
        os.close(descriptor)
        raise ValueError(f"Synchronization lock is not a regular file: {path}")
    os.fchmod(descriptor, 0o600)
    handle: BinaryIO = os.fdopen(descriptor, "a+b")
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise SyncLockError(
                        f"Another bookmark sync is still running (lock: {path})"
                    )
                time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


APPLICATION_EXECUTABLES = {
    "Google Chrome": {
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    },
    "Safari": {
        "/Applications/Safari.app/Contents/MacOS/Safari",
        "/System/Applications/Safari.app/Contents/MacOS/Safari",
        "/System/Cryptexes/App/System/Applications/Safari.app/Contents/MacOS/Safari",
    },
}


def executable_is_running(executables: set[str]) -> bool:
    result = subprocess.run(
        ["/bin/ps", "-axo", "comm="],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise BrowserRunningError(
            "Unable to inspect running applications; no bookmark files were written."
        )
    return any(line.strip() in executables for line in result.stdout.splitlines())


def process_is_running(name: str) -> bool:
    return executable_is_running(APPLICATION_EXECUTABLES.get(name, {name}))


def ensure_destination_browsers_closed(
    plan: SyncPlan,
    chrome_target: Path,
    safari_path: Path,
    allow_running: bool,
) -> None:
    if allow_running:
        return
    resolved_chrome_target = chrome_target.resolve()
    resolved_chrome_root = DEFAULT_CHROME_ROOT.resolve()
    resolved_safari_path = safari_path.resolve()
    resolved_default_safari = DEFAULT_SAFARI_BOOKMARKS.resolve()
    running: list[str] = []
    if (
        plan.chrome_to_safari
        and resolved_safari_path == resolved_default_safari
        and process_is_running("Safari")
    ):
        running.append("Safari")
    if (
        plan.safari_to_chrome
        and resolved_chrome_root in resolved_chrome_target.parents
        and process_is_running("Google Chrome")
    ):
        running.append("Google Chrome")
    if running:
        names = ", ".join(running)
        raise BrowserRunningError(
            f"Close the destination browser(s) before writing: {names}. "
            "Use --allow-running-browsers only if you accept overwrite risk."
        )


def run_sync(args: argparse.Namespace) -> int:
    chrome_paths, chrome_target, safari_path = resolve_paths(args)
    lock_path = (
        args.lock_file.expanduser()
        if args.lock_file
        else safari_path.parent / ".chrome-safari-bookmarks-sync.lock"
    )
    with acquire_sync_lock(lock_path, args.lock_timeout):
        loaded_chrome = {path: load_json_snapshot(path) for path in chrome_paths}
        chrome_snapshots = {path: snapshot[0] for path, snapshot in loaded_chrome.items()}
        chrome_fingerprints = {
            path: snapshot[1] for path, snapshot in loaded_chrome.items()
        }
        read_stats: dict[str, int] = {}
        chrome_bookmarks = read_chrome_bookmarks_from_data(
            chrome_snapshots,
            args.include_active_bookmarks,
            read_stats,
        )
        safari_data, safari_fingerprint = load_plist_snapshot(safari_path)
        safari_bookmarks = read_safari_bookmarks(
            safari_data,
            safari_path,
            args.include_active_bookmarks,
            read_stats,
        )

        plan = plan_sync(
            chrome_bookmarks,
            safari_bookmarks,
            chrome_target,
            args.mode,
            args.dedup_policy,
            read_stats.get("active_skipped", 0),
        )
        if not args.quiet:
            print(
                format_plan(
                    plan,
                    args.apply,
                    args.mode,
                    args.preview_limit,
                    args.dedup_policy,
                )
            )

        if not args.apply:
            if not args.quiet:
                print("")
                print("No files changed. Re-run with --apply to write bookmark files.")
            return 0

        if args.backup_retention < 1:
            raise ValueError("--backup-retention must be at least 1")

        ensure_destination_browsers_closed(
            plan,
            chrome_target,
            safari_path,
            args.allow_running_browsers,
        )

        chrome_data: Optional[dict] = None
        chrome_fingerprint: Optional[FileFingerprint] = None
        if plan.chrome_to_safari:
            safari_folder_index: dict[int, dict[str, dict]] = {}
            for bookmark in plan.chrome_to_safari:
                add_bookmark_under_target(
                    safari_data,
                    args.safari_target_folder,
                    bookmark,
                    safari_folder_index,
                )

        if plan.safari_to_chrome:
            chrome_data = chrome_snapshots[chrome_target]
            chrome_fingerprint = chrome_fingerprints[chrome_target]
            max_id = max(
                (chrome_max_id(root) for root in chrome_data.get("roots", {}).values()),
                default=0,
            )
            counter = [max_id]
            chrome_folder_index: dict[int, dict[str, dict]] = {}
            for bookmark in plan.safari_to_chrome:
                add_safari_bookmark_to_chrome(
                    chrome_data,
                    args.chrome_target_folder,
                    bookmark,
                    counter,
                    chrome_folder_index,
                )

        if plan.chrome_to_safari or plan.safari_to_chrome:
            for path, fingerprint in chrome_fingerprints.items():
                assert_fingerprint(path, fingerprint)
            assert_fingerprint(safari_path, safari_fingerprint)

        backups: list[Path] = []
        if plan.chrome_to_safari:
            backups.append(
                save_plist_atomic(
                    safari_data,
                    safari_path,
                    safari_fingerprint,
                    args.backup_retention,
                )
            )
        if (
            plan.safari_to_chrome
            and chrome_data is not None
            and chrome_fingerprint is not None
        ):
            backups.append(
                save_json_atomic(
                    chrome_data,
                    chrome_target,
                    chrome_fingerprint,
                    args.backup_retention,
                )
            )

        if not args.quiet:
            print("")
            if backups:
                print("Wrote bookmark files. Backups:")
                for path in backups:
                    print(f"- {path}")
            else:
                print("No bookmark files changed.")
        return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        return run_sync(args)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except PermissionError as exc:
        print(f"Permission denied: {exc.filename}", file=sys.stderr)
        print(
            "Grant Full Disk Access to your terminal app or /usr/bin/python3, then retry.",
            file=sys.stderr,
        )
        return 3
    except (
        ConcurrentModificationError,
        SyncLockError,
        BrowserRunningError,
        ValueError,
        plistlib.InvalidFileException,
    ) as exc:
        print(f"Sync aborted safely: {exc}", file=sys.stderr)
        return 4
    except OSError as exc:
        print(f"Sync aborted safely due to an operating system error: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
