import json
import plistlib
import tempfile
import unittest
from pathlib import Path

from chrome_to_safari import (
    Bookmark,
    add_bookmark_under_target,
    add_safari_bookmark_to_chrome,
    chrome_max_id,
    load_json,
    load_plist,
    main,
    normalize_url,
    plan_sync,
    read_chrome_bookmarks,
    read_safari_bookmarks,
    refresh_chrome_checksum,
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
                        "url": "https://www.example.com/?utm_source=x",
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
                        "URLString": "https://www.safari-only.example/",
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
    def test_normalize_url_removes_tracking_and_www(self):
        self.assertEqual(
            normalize_url("https://www.Example.com/path/?utm_source=x&b=2&a=1"),
            "https://example.com/path?a=1&b=2",
        )

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

    def test_chrome_checksum_is_refreshed_after_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            chrome_path = Path(tmp) / "Bookmarks"
            safari_path = Path(tmp) / "Bookmarks.plist"
            chrome_fixture(chrome_path)
            safari_fixture(safari_path)

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


if __name__ == "__main__":
    unittest.main()
