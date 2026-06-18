import json
import io
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from chrome_to_safari import (
    Bookmark,
    BrowserRunningError,
    ConcurrentModificationError,
    SyncLockError,
    acquire_sync_lock,
    add_bookmark_under_target,
    add_safari_bookmark_to_chrome,
    chrome_max_id,
    discover_chrome_bookmarks,
    ensure_destination_browsers_closed,
    executable_is_running,
    load_json,
    load_json_snapshot,
    load_plist,
    main,
    normalize_url,
    plan_sync,
    read_chrome_bookmarks,
    read_safari_bookmarks,
    refresh_chrome_checksum,
    save_json_atomic,
)


def chrome_fixture(path: Path) -> None:
    data = {
        "roots": {
            "bookmark_bar": {
                "type": "folder",
                "name": "Bookmarks Bar",
                "children": [
                    {
                        "id": "10",
                        "type": "url",
                        "name": "Example",
                        "url": "HTTPS://EXAMPLE.COM:443",
                    },
                    {
                        "id": "11",
                        "type": "url",
                        "name": "Example duplicate",
                        "url": "https://example.com/",
                    },
                    {
                        "id": "12",
                        "type": "folder",
                        "name": "Docs",
                        "children": [
                            {
                                "id": "13",
                                "type": "url",
                                "name": "Python",
                                "url": "https://www.python.org/doc/",
                            }
                        ],
                    },
                ],
            },
            "other": {
                "id": "2",
                "type": "folder",
                "name": "Other Bookmarks",
                "children": [],
            },
        }
    }
    path.write_text(json.dumps(data), encoding="utf-8")


def safari_fixture(path: Path) -> None:
    data = {
        "Children": [
            {
                "Children": [
                    {
                        "URIDictionary": {"title": "Existing"},
                        "URLString": "https://example.com",
                        "WebBookmarkType": "WebBookmarkTypeLeaf",
                    },
                    {
                        "URIDictionary": {"title": "Safari Only"},
                        "URLString": "https://safari-only.example",
                        "WebBookmarkType": "WebBookmarkTypeLeaf",
                    },
                    {
                        "URIDictionary": {"title": "Safari Only Duplicate"},
                        "URLString": "HTTPS://SAFARI-ONLY.EXAMPLE:443",
                        "WebBookmarkType": "WebBookmarkTypeLeaf",
                    },
                ],
                "Title": "BookmarksBar",
                "WebBookmarkType": "WebBookmarkTypeList",
            }
        ]
    }
    with path.open("wb") as handle:
        plistlib.dump(data, handle, fmt=plistlib.FMT_BINARY)


class ChromeSafariSyncTests(unittest.TestCase):
    def test_conservative_normalization_only_removes_safe_equivalences(self):
        self.assertEqual(
            normalize_url("HTTPS://Example.com:443"),
            "https://example.com/",
        )
        original = "https://www.Example.com/path/?utm_source=x&b=2&a=1#section"
        self.assertEqual(
            normalize_url(original),
            "https://www.example.com/path/?utm_source=x&b=2&a=1#section",
        )

    def test_tracking_policy_is_explicit_and_preserves_semantic_parameters(self):
        self.assertEqual(
            normalize_url(
                "https://example.com/?utm_source=x&ref=docs&b=2#section",
                "tracking",
            ),
            "https://example.com/?ref=docs&b=2#section",
        )

    def test_www_query_order_and_fragments_remain_distinct(self):
        values = {
            normalize_url("https://example.com/?a=1&b=2"),
            normalize_url("https://www.example.com/?a=1&b=2"),
            normalize_url("https://example.com/?b=2&a=1"),
            normalize_url("https://example.com/?a=1&b=2#section"),
        }
        self.assertEqual(len(values), 4)

    def test_active_bookmarks_are_skipped_unless_explicitly_enabled(self):
        source = Path("/tmp/Bookmarks.plist")
        data = {
            "Children": [
                {"URLString": "javascript:alert(1)"},
                {"URLString": "data:text/plain,hello"},
                {"URLString": "https://example.com"},
            ]
        }
        stats: dict[str, int] = {}

        safe = read_safari_bookmarks(data, source, stats=stats)
        all_bookmarks = read_safari_bookmarks(
            data,
            source,
            include_active=True,
        )

        self.assertEqual([item.url for item in safe], ["https://example.com"])
        self.assertEqual(stats["active_skipped"], 2)
        self.assertEqual(len(all_bookmarks), 3)

    def test_plan_sync_skips_duplicates_in_both_directions(self):
        with tempfile.TemporaryDirectory() as tmp:
            chrome_path = Path(tmp) / "Bookmarks"
            safari_path = Path(tmp) / "Bookmarks.plist"
            chrome_fixture(chrome_path)
            safari_fixture(safari_path)

            chrome = read_chrome_bookmarks([chrome_path])
            safari_data = load_plist(safari_path)
            safari = read_safari_bookmarks(safari_data, safari_path)
            plan = plan_sync(chrome, safari, chrome_path, "both")

            self.assertEqual(plan.chrome_duplicates, 1)
            self.assertEqual(plan.safari_duplicates, 1)
            self.assertEqual([item.title for item in plan.chrome_to_safari], ["Python"])
            self.assertEqual([item.title for item in plan.safari_to_chrome], ["Safari Only"])

    def test_apply_both_directions(self):
        with tempfile.TemporaryDirectory() as tmp:
            chrome_path = Path(tmp) / "Bookmarks"
            safari_path = Path(tmp) / "Bookmarks.plist"
            chrome_fixture(chrome_path)
            safari_fixture(safari_path)

            with redirect_stdout(io.StringIO()):
                rc = main(
                    [
                        "--chrome-bookmarks",
                        str(chrome_path),
                        "--safari-bookmarks",
                        str(safari_path),
                        "--apply",
                    ]
                )

            self.assertEqual(rc, 0)
            safari = load_plist(safari_path)
            imported = next(
                child
                for child in safari["Children"]
                if child.get("Title") == "Imported from Google Chrome"
            )
            bar = next(child for child in imported["Children"] if child.get("Title") == "Bookmarks Bar")
            docs = next(child for child in bar["Children"] if child.get("Title") == "Docs")
            self.assertEqual(docs["Children"][0]["URLString"], "https://www.python.org/doc/")

            chrome = load_json(chrome_path)
            other = chrome["roots"]["other"]
            imported_to_chrome = next(
                child for child in other["children"] if child.get("name") == "Imported from Safari"
            )
            safari_bar = next(
                child
                for child in imported_to_chrome["children"]
                if child.get("name") == "Safari Bookmarks Bar"
            )
            self.assertEqual(safari_bar["children"][0]["url"], "https://safari-only.example")

    def test_direction_modes(self):
        with tempfile.TemporaryDirectory() as tmp:
            chrome_path = Path(tmp) / "Bookmarks"
            safari_path = Path(tmp) / "Bookmarks.plist"
            chrome_fixture(chrome_path)
            safari_fixture(safari_path)

            chrome = read_chrome_bookmarks([chrome_path])
            safari_data = load_plist(safari_path)
            safari = read_safari_bookmarks(safari_data, safari_path)

            c2s = plan_sync(chrome, safari, chrome_path, "chrome-to-safari")
            s2c = plan_sync(chrome, safari, chrome_path, "safari-to-chrome")

            self.assertEqual(len(c2s.chrome_to_safari), 1)
            self.assertEqual(len(c2s.safari_to_chrome), 0)
            self.assertEqual(len(s2c.chrome_to_safari), 0)
            self.assertEqual(len(s2c.safari_to_chrome), 1)

    def test_add_bookmark_helper_keeps_existing_target(self):
        safari = {"Children": []}
        chrome_path = Path("/tmp/Bookmarks")
        add_bookmark_under_target(
            safari,
            "Imported",
            Bookmark("A", "https://a.example", ("Bar",), chrome_path),
        )
        add_bookmark_under_target(
            safari,
            "Imported",
            Bookmark("B", "https://b.example", ("Bar",), chrome_path),
        )
        imported = safari["Children"][0]
        self.assertEqual(len(imported["Children"]), 1)
        self.assertEqual(len(imported["Children"][0]["Children"]), 2)

    def test_add_safari_bookmark_to_chrome_uses_incrementing_ids(self):
        chrome = {"roots": {"other": {"id": "2", "type": "folder", "children": []}}}
        counter = [chrome_max_id(chrome["roots"]["other"])]
        add_safari_bookmark_to_chrome(
            chrome,
            "Imported from Safari",
            Bookmark("A", "https://a.example", ("Safari Bar",), Path("/tmp/Safari.plist")),
            counter,
        )
        imported = chrome["roots"]["other"]["children"][0]
        leaf = imported["children"][0]["children"][0]
        self.assertEqual(imported["id"], "3")
        self.assertEqual(leaf["id"], "5")
        self.assertEqual(leaf["url"], "https://a.example")
        self.assertIn("date_modified", chrome["roots"]["other"])
        self.assertIn("date_modified", imported)

    def test_chrome_checksum_is_refreshed_after_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            chrome_path = Path(tmp) / "Bookmarks"
            safari_path = Path(tmp) / "Bookmarks.plist"
            chrome_fixture(chrome_path)
            safari_fixture(safari_path)

            with redirect_stdout(io.StringIO()):
                main(
                    [
                        "--chrome-bookmarks",
                        str(chrome_path),
                        "--safari-bookmarks",
                        str(safari_path),
                        "--mode",
                        "safari-to-chrome",
                        "--apply",
                    ]
                )

            data = load_json(chrome_path)
            stored = data["checksum"]
            self.assertEqual(stored, refresh_chrome_checksum(data))

    def test_discovers_all_profiles_and_prefers_default_as_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for profile in ("Profile 12", "Default", "Guest Profile"):
                directory = root / profile
                directory.mkdir()
                chrome_fixture(directory / "Bookmarks")
            (root / "Profile Symlink").symlink_to(root / "Default")

            paths = discover_chrome_bookmarks(root)

            self.assertEqual(paths[0].parent.name, "Default")
            self.assertEqual(
                {path.parent.name for path in paths},
                {"Default", "Guest Profile", "Profile 12"},
            )

    def test_rejects_write_when_source_changed_after_planning(self):
        with tempfile.TemporaryDirectory() as tmp:
            chrome_path = Path(tmp) / "Bookmarks"
            chrome_fixture(chrome_path)
            data, fingerprint = load_json_snapshot(chrome_path)
            original = chrome_path.read_bytes()
            chrome_path.write_bytes(original + b"\n")

            with self.assertRaises(ConcurrentModificationError):
                save_json_atomic(data, chrome_path, fingerprint)

            self.assertEqual(chrome_path.read_bytes(), original + b"\n")
            self.assertEqual(list(Path(tmp).glob("Bookmarks.bak-*")), [])

    def test_sync_lock_rejects_overlapping_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "sync.lock"
            with acquire_sync_lock(lock, 0):
                with self.assertRaises(SyncLockError):
                    with acquire_sync_lock(lock, 0):
                        pass
            self.assertEqual(os.stat(lock).st_mode & 0o777, 0o600)

    def test_sync_lock_rejects_symbolic_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target"
            target.touch()
            lock = Path(tmp) / "sync.lock"
            lock.symlink_to(target)

            with self.assertRaises(ValueError):
                with acquire_sync_lock(lock, 0):
                    pass

    def test_atomic_write_preserves_mode_and_rotates_backups(self):
        with tempfile.TemporaryDirectory() as tmp:
            chrome_path = Path(tmp) / "Bookmarks"
            chrome_fixture(chrome_path)
            os.chmod(chrome_path, 0o640)

            for _ in range(4):
                data, fingerprint = load_json_snapshot(chrome_path)
                save_json_atomic(data, chrome_path, fingerprint, retention=2)

            self.assertEqual(os.stat(chrome_path).st_mode & 0o777, 0o640)
            self.assertEqual(len(list(Path(tmp).glob("Bookmarks.bak-*"))), 2)

    def test_malformed_safari_title_dictionary_does_not_crash(self):
        source = Path("/tmp/Bookmarks.plist")
        data = {
            "Children": [
                {
                    "URLString": "https://example.com",
                    "URIDictionary": "invalid",
                }
            ]
        }

        bookmarks = read_safari_bookmarks(data, source)

        self.assertEqual(bookmarks[0].title, "https://example.com")

    def test_malformed_chrome_roots_abort_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            chrome_path = Path(tmp) / "Bookmarks"
            safari_path = Path(tmp) / "Bookmarks.plist"
            chrome_path.write_text('{"roots": []}', encoding="utf-8")
            safari_fixture(safari_path)

            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                result = main(
                    [
                        "--chrome-bookmarks",
                        str(chrome_path),
                        "--safari-bookmarks",
                        str(safari_path),
                    ]
                )

            self.assertEqual(result, 4)

    def test_running_destination_browser_blocks_default_path_write(self):
        bookmark = Bookmark(
            "Safari only",
            "https://example.com",
            (),
            Path("/tmp/Bookmarks.plist"),
        )
        plan = plan_sync(
            [],
            [bookmark],
            Path.home()
            / "Library/Application Support/Google/Chrome/Default/Bookmarks",
            "both",
        )

        with patch("chrome_to_safari.process_is_running", return_value=True):
            with self.assertRaises(BrowserRunningError):
                ensure_destination_browsers_closed(
                    plan,
                    plan.chrome_target,
                    Path.home() / "Library/Safari/Bookmarks.plist",
                    allow_running=False,
                )

    def test_running_browser_check_can_be_explicitly_overridden(self):
        bookmark = Bookmark(
            "Safari only",
            "https://example.com",
            (),
            Path("/tmp/Bookmarks.plist"),
        )
        plan = plan_sync(
            [],
            [bookmark],
            Path.home()
            / "Library/Application Support/Google/Chrome/Default/Bookmarks",
            "both",
        )

        with patch("chrome_to_safari.process_is_running", return_value=True):
            ensure_destination_browsers_closed(
                plan,
                plan.chrome_target,
                Path.home() / "Library/Safari/Bookmarks.plist",
                allow_running=True,
            )

    def test_running_process_detection_uses_exact_executable_path(self):
        process = subprocess.Popen(["/bin/sleep", "5"])
        try:
            self.assertTrue(executable_is_running({"/bin/sleep"}))
            self.assertFalse(executable_is_running({"/bin/sleep-other"}))
        finally:
            process.terminate()
            process.wait(timeout=5)

    def test_launch_agent_generation_hides_urls_and_watches_all_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            chrome_root = (
                home / "Library/Application Support/Google/Chrome"
            )
            for profile in ("Default", "Profile 9"):
                directory = chrome_root / profile
                directory.mkdir(parents=True)
                chrome_fixture(directory / "Bookmarks")
            safari_path = home / "Library/Safari/Bookmarks.plist"
            safari_path.parent.mkdir(parents=True)
            safari_fixture(safari_path)

            bin_dir = home / "bin"
            bin_dir.mkdir()
            launchctl = bin_dir / "launchctl"
            launchctl.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            launchctl.chmod(0o755)
            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(home),
                    "PATH": f"{bin_dir}:{env['PATH']}",
                    "PYTHON": sys.executable,
                }
            )

            subprocess.run(
                ["./install_launch_agent.sh"],
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                check=True,
                stdout=subprocess.DEVNULL,
            )

            plist_path = (
                home
                / "Library/LaunchAgents/com.local.chrome-to-safari-bookmarks.plist"
            )
            data = load_plist(plist_path)
            arguments = data["ProgramArguments"]
            self.assertIn("--preview-limit", arguments)
            self.assertEqual(arguments[arguments.index("--preview-limit") + 1], "0")
            self.assertIn("--quiet", arguments)
            self.assertNotIn("--allow-running-browsers", arguments)
            self.assertEqual(data["StartInterval"], 300)
            self.assertEqual(
                set(data["WatchPaths"]),
                {
                    str(chrome_root / "Default/Bookmarks"),
                    str(chrome_root / "Profile 9/Bookmarks"),
                    str(safari_path),
                },
            )
            self.assertEqual(os.stat(plist_path).st_mode & 0o777, 0o600)
            for name in (
                "chrome-to-safari-bookmarks.log",
                "chrome-to-safari-bookmarks.err.log",
            ):
                log_path = home / "Library/Logs" / name
                self.assertEqual(os.stat(log_path).st_mode & 0o777, 0o600)

    def test_quiet_mode_suppresses_normal_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            chrome_path = Path(tmp) / "Bookmarks"
            safari_path = Path(tmp) / "Bookmarks.plist"
            chrome_fixture(chrome_path)
            safari_fixture(safari_path)
            output = io.StringIO()

            with redirect_stdout(output):
                result = main(
                    [
                        "--chrome-bookmarks",
                        str(chrome_path),
                        "--safari-bookmarks",
                        str(safari_path),
                        "--quiet",
                    ]
                )

            self.assertEqual(result, 0)
            self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
