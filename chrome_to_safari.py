#!/usr/bin/env python3
"""Bidirectional Chrome and Safari bookmark sync for macOS.

Default mode is a dry run. Use --apply to write bookmark files.
The sync is conservative: it only adds missing URLs and never deletes,
renames, or moves existing bookmarks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import shutil
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


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
    "ref",
    "ref_src",
    "ref_url",
    "source",
    "via",
}


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


def normalize_url(url: str) -> str:
    """Normalize URLs for duplicate detection, not for writing."""
    try:
        parsed = urlparse(url.strip())
        scheme = (parsed.scheme or "https").lower()
        netloc = parsed.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        if netloc.endswith(":80") and scheme == "http":
            netloc = netloc[:-3]
        if netloc.endswith(":443") and scheme == "https":
            netloc = netloc[:-4]
        path = parsed.path.rstrip("/") or "/"
        params = sorted(
            (k, v)
            for k, v in parse_qsl(parsed.query, keep_blank_values=True)
            if k.lower() not in TRACKING_PARAMS
        )
        return urlunparse((scheme, netloc, path, "", urlencode(params), ""))
    except Exception:
        return url.strip().lower()


def chrome_timestamp() -> str:
    epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return str(int((now - epoch).total_seconds() * 1_000_000))


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_plist(path: Path) -> dict:
    with path.open("rb") as handle:
        return plistlib.load(handle)


def backup_path(path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return path.with_name(f"{path.name}.bak-{timestamp}")


def save_json_atomic(data: dict, path: Path) -> Path:
    refresh_chrome_checksum(data)
    backup = backup_path(path)
    shutil.copy2(path, backup)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as tmp:
        json.dump(data, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)
    return backup


def save_plist_atomic(data: dict, path: Path) -> Path:
    backup = backup_path(path)
    shutil.copy2(path, backup)
    with tempfile.NamedTemporaryFile("wb", delete=False, dir=str(path.parent)) as tmp:
        plistlib.dump(data, tmp, fmt=plistlib.FMT_BINARY, sort_keys=False)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)
    return backup


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


def refresh_chrome_checksum(data: dict) -> str:
    md5 = hashlib.md5()
    roots = data.get("roots") or {}
    for key in ("bookmark_bar", "other", "synced"):
        node = roots.get(key)
        if isinstance(node, dict):
            update_chrome_checksum_for_node(md5, node)
    checksum = md5.hexdigest()
    data["checksum"] = checksum
    return checksum


def discover_chrome_bookmarks(chrome_root: Path) -> list[Path]:
    candidates: list[Path] = []
    for name in ("Default", "Profile 1", "Profile 2", "Profile 3", "Profile 4"):
        path = chrome_root / name / "Bookmarks"
        if path.exists():
            candidates.append(path)
    return candidates


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
) -> None:
    node_type = node.get("type")
    if node_type == "url" and node.get("url"):
        url = str(node["url"]).strip()
        if url.startswith(("chrome://", "chrome-extension://", "about:")):
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
            walk_chrome_node(child, next_path, source, out)


def read_chrome_bookmarks(paths: list[Path]) -> list[Bookmark]:
    bookmarks: list[Bookmark] = []
    for path in paths:
        data = load_json(path)
        roots = data.get("roots") or {}
        for key, node in roots.items():
            if not isinstance(node, dict):
                continue
            root_path = (chrome_root_label(key, node),)
            for child in node.get("children") or []:
                if isinstance(child, dict):
                    walk_chrome_node(child, root_path, path, bookmarks)
    return bookmarks


def walk_safari_node(
    node: object,
    path: tuple[str, ...],
    source: Path,
    out: list[Bookmark],
) -> None:
    if not isinstance(node, dict):
        return
    url = node.get("URLString")
    if isinstance(url, str) and url.strip():
        title = node.get("URIDictionary", {}).get("title") or url
        out.append(Bookmark(str(title).strip() or url, url.strip(), path, source))
        return

    title = str(node.get("Title") or "").strip()
    next_path = path
    if title:
        next_path = path + (safari_root_label(title),)
    for child in node.get("Children") or []:
        walk_safari_node(child, next_path, source, out)


def read_safari_bookmarks(data: dict, source: Path) -> list[Bookmark]:
    bookmarks: list[Bookmark] = []
    for child in data.get("Children") or []:
        walk_safari_node(child, (), source, bookmarks)
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


def ensure_safari_folder(parent: dict, title: str) -> dict:
    parent.setdefault("Children", [])
    existing = find_child_folder(parent, title)
    if existing is not None:
        return existing
    folder = new_safari_folder(title)
    parent["Children"].append(folder)
    return folder


def add_bookmark_under_target(root: dict, target_name: str, bookmark: Bookmark) -> None:
    target = ensure_safari_folder(root, target_name)
    folder = target
    for part in bookmark.path:
        folder = ensure_safari_folder(folder, part)
    folder.setdefault("Children", []).append(new_safari_leaf(bookmark))


def deduplicate_bookmarks(bookmarks: list[Bookmark]) -> tuple[list[Bookmark], int]:
    seen: set[str] = set()
    unique: list[Bookmark] = []
    duplicate_count = 0
    for bookmark in bookmarks:
        normalized = normalize_url(bookmark.url)
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
) -> SyncPlan:
    unique_chrome, chrome_duplicate_count = deduplicate_bookmarks(chrome_bookmarks)
    unique_safari, safari_duplicate_count = deduplicate_bookmarks(safari_bookmarks)
    chrome_urls = {normalize_url(bookmark.url) for bookmark in unique_chrome}
    safari_urls = {normalize_url(bookmark.url) for bookmark in unique_safari}

    chrome_to_safari: list[Bookmark] = []
    safari_to_chrome: list[Bookmark] = []
    if mode in ("both", "chrome-to-safari"):
        chrome_to_safari = [
            bookmark for bookmark in unique_chrome if normalize_url(bookmark.url) not in safari_urls
        ]
    if mode in ("both", "safari-to-chrome"):
        safari_to_chrome = [
            bookmark for bookmark in unique_safari if normalize_url(bookmark.url) not in chrome_urls
        ]

    return SyncPlan(
        chrome_to_safari=chrome_to_safari,
        safari_to_chrome=safari_to_chrome,
        chrome_duplicates=chrome_duplicate_count,
        safari_duplicates=safari_duplicate_count,
        chrome_target=chrome_target,
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


def ensure_chrome_root(data: dict, key: str) -> dict:
    roots = data.setdefault("roots", {})
    root = roots.get(key)
    if not isinstance(root, dict):
        root = {
            "children": [],
            "date_added": chrome_timestamp(),
            "date_last_used": "0",
            "date_modified": chrome_timestamp(),
            "guid": str(uuid.uuid4()),
            "id": "2",
            "name": "Other Bookmarks",
            "type": "folder",
        }
        roots[key] = root
    root.setdefault("children", [])
    return root


def ensure_chrome_folder(parent: dict, name: str, counter: list[int]) -> dict:
    parent.setdefault("children", [])
    existing = find_chrome_folder(parent, name)
    if existing is not None:
        return existing
    folder = new_chrome_folder(name, counter)
    parent["children"].append(folder)
    return folder


def add_safari_bookmark_to_chrome(
    chrome_data: dict,
    target_folder: str,
    bookmark: Bookmark,
    counter: list[int],
) -> None:
    root = ensure_chrome_root(chrome_data, "other")
    folder = ensure_chrome_folder(root, target_folder, counter)
    for part in bookmark.path:
        folder = ensure_chrome_folder(folder, part, counter)
    folder.setdefault("children", []).append(new_chrome_leaf(bookmark, counter))


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


def format_plan(plan: SyncPlan, apply: bool, mode: str, preview_limit: int) -> str:
    lines = [
        f"Mode: {'apply' if apply else 'dry-run'}",
        f"Sync direction: {mode}",
        f"Chrome duplicates skipped: {plan.chrome_duplicates}",
        f"Safari duplicates skipped: {plan.safari_duplicates}",
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
        "--apply",
        action="store_true",
        help="Write changes. Without this flag the command is a dry run.",
    )
    parser.add_argument(
        "--preview-limit",
        type=int,
        default=20,
        help="Maximum number of bookmark URLs to print per direction. Use 0 to hide URLs. Default: 20.",
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


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        chrome_paths, chrome_target, safari_path = resolve_paths(args)
        chrome_bookmarks = read_chrome_bookmarks(chrome_paths)
        safari_data = load_plist(safari_path)
        safari_bookmarks = read_safari_bookmarks(safari_data, safari_path)
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

    plan = plan_sync(chrome_bookmarks, safari_bookmarks, chrome_target, args.mode)
    print(format_plan(plan, args.apply, args.mode, args.preview_limit))

    if not args.apply:
        print("")
        print("No files changed. Re-run with --apply to write bookmark files.")
        return 0

    backups: list[Path] = []
    if plan.chrome_to_safari:
        for bookmark in plan.chrome_to_safari:
            add_bookmark_under_target(safari_data, args.safari_target_folder, bookmark)
        backups.append(save_plist_atomic(safari_data, safari_path))

    if plan.safari_to_chrome:
        chrome_data = load_json(chrome_target)
        max_id = max(chrome_max_id(root) for root in chrome_data.get("roots", {}).values())
        counter = [max_id]
        for bookmark in plan.safari_to_chrome:
            add_safari_bookmark_to_chrome(
                chrome_data,
                args.chrome_target_folder,
                bookmark,
                counter,
            )
        backups.append(save_json_atomic(chrome_data, chrome_target))

    print("")
    if backups:
        print("Wrote bookmark files. Backups:")
        for path in backups:
            print(f"- {path}")
    else:
        print("No bookmark files changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
