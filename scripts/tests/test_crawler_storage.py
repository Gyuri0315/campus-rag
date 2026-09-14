from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.crawlers.common.storage import (
    StateLoadError, archive_document, empty_state, find_document_json,
    get_dataset_paths, load_state_with_migration, safe_component,
    save_state_atomic, state_migration_plan, write_document,
)


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


class CrawlerStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=WORKSPACE_ROOT)
        self.root = Path(self.temp.name)
        self.paths = get_dataset_paths(self.root, "pknu_notice")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_new_state_and_atomic_save(self) -> None:
        state, origin = load_state_with_migration(self.paths)
        self.assertEqual("new", origin)
        state["items"]["1"] = {"slug": "one", "content_hash": "abc"}
        save_state_atomic(self.paths.state, state, "pknu_notice")
        payload = json.loads(self.paths.state.read_text(encoding="utf-8"))
        self.assertEqual("1.0", payload["schema_version"])
        self.assertEqual("pknu_notice", payload["dataset"])
        self.assertFalse(list(self.paths.state.parent.glob("*.tmp")))
        for field in ("source_id", "slug", "content_hash", "last_seen_at", "miss_count", "status"):
            self.assertIn(field, payload["items"]["1"])

    def test_legacy_state_is_copied_without_deleting_source(self) -> None:
        legacy = self.root / "state_pknu_notice.json"
        legacy.write_text(json.dumps({"posts": {"7": {"slug": "seven"}}}), encoding="utf-8")
        state, origin = load_state_with_migration(
            self.paths, legacy_paths=(legacy,), legacy_kind="posts"
        )
        self.assertTrue(origin.startswith("migrated:"))
        self.assertTrue(legacy.exists())
        self.assertTrue(self.paths.state.exists())
        self.assertEqual("seven", state["items"]["7"]["slug"])

    def test_legacy_state_timestamps_are_normalized_to_kst(self) -> None:
        legacy = self.root / "state.json"
        legacy.write_text(json.dumps({
            "1": {"last_seen_at": "2026-09-02T10:00:00", "updated_at": "2026-09-02T01:00:00Z"}
        }), encoding="utf-8")
        state, _ = load_state_with_migration(self.paths, legacy_paths=(legacy,))
        self.assertEqual("2026-09-02T10:00:00+09:00", state["items"]["1"]["last_seen_at"])
        self.assertEqual("2026-09-02T10:00:00+09:00", state["items"]["1"]["updated_at"])

    def test_corrupt_current_uses_recoverable_backup(self) -> None:
        self.paths.state.parent.mkdir(parents=True)
        self.paths.state.write_text("{broken", encoding="utf-8")
        backup = self.paths.state.with_suffix(".json.bak")
        backup.write_text(json.dumps(empty_state("pknu_notice")), encoding="utf-8")
        _, origin = load_state_with_migration(self.paths)
        self.assertEqual("backup", origin)
        self.assertEqual("{broken", self.paths.state.read_text(encoding="utf-8"))

    def test_corrupt_state_without_valid_backup_raises(self) -> None:
        self.paths.state.parent.mkdir(parents=True)
        self.paths.state.write_text("{broken", encoding="utf-8")
        with self.assertRaises(StateLoadError):
            load_state_with_migration(self.paths)

    def test_existing_target_causes_non_destructive_collision_skip(self) -> None:
        save_state_atomic(self.paths.state, empty_state("pknu_notice"), "pknu_notice")
        legacy = self.root / "state_pknu_notice.json"
        legacy.write_text("{}", encoding="utf-8")
        plan = state_migration_plan(self.paths, (legacy,))
        self.assertEqual("skip", plan[0]["action"])
        self.assertEqual("target_exists", plan[0]["reason"])

    def test_dry_run_plan_does_not_change_files(self) -> None:
        legacy = self.root / "state_pknu_notice.json"
        legacy.write_text("{}", encoding="utf-8")
        before = legacy.read_bytes()
        plan = state_migration_plan(self.paths, (legacy,))
        self.assertEqual("copy_state", plan[0]["action"])
        self.assertFalse(self.paths.state.exists())
        self.assertEqual(before, legacy.read_bytes())

    def test_existing_output_fallback(self) -> None:
        legacy_root = self.root / "legacy" / "json"
        legacy_doc = legacy_root / "category" / "slug.json"
        legacy_doc.parent.mkdir(parents=True)
        legacy_doc.write_text("{}", encoding="utf-8")
        self.assertEqual(legacy_doc, find_document_json(self.paths, "slug", (legacy_root,)))

    def test_archive_moves_json_html_and_files_without_overwrite(self) -> None:
        write_document(self.paths, {"slug": "s"}, "cat", "s", "<html></html>")
        attachment = self.paths.attachment_dir("cat", "s") / "a.pdf"
        attachment.parent.mkdir(parents=True)
        attachment.write_bytes(b"pdf")
        moved = archive_document(self.paths, "cat", "s")
        self.assertEqual({"json", "html", "files"}, set(moved))
        with self.assertRaises(FileExistsError):
            write_document(self.paths, {"slug": "s"}, "cat", "s", "x")
            archive_document(self.paths, "cat", "s")

    def test_windows_and_traversal_components(self) -> None:
        self.assertEqual("category", safe_component(Path("category"), "category"))
        with self.assertRaises(ValueError):
            safe_component("..\\outside", "category")
        with self.assertRaises(ValueError):
            safe_component("C:outside", "category")
        with self.assertRaises(ValueError):
            safe_component("CON", "category")


if __name__ == "__main__":
    unittest.main()
