import json
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path

from abs_waterfall_check import local
from abs_waterfall_check.access import Access, password_hash
from abs_waterfall_check.web import App
from abs_waterfall_check.web_examples import seed_examples
from scripts.instance_backup import create, restore


class OfflineBackupTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.base = Path(folder.name)
        self.instance = self.base / "instance"
        self.store = self.instance / "store"
        seed_examples(self.store)
        self.archive = self.base / "backup.zip"
        self.dest = self.base / "restored"
        local.save(self.instance / "users.json", {"organization": "Test team", "users": {
            "Test reviewer": {"role": "reviewer", "password_hash": password_hash("example password")}}})

    def test_restored_private_accounts_and_exact_saved_audit(self):
        report = App(self.store).post("/api/deals/mastr/runs", {"mode": "check", "case_id": "mastr-original"})
        (self.instance / "omit-from-backup").touch()
        create(self.instance, self.archive)
        with self.assertRaises(FileExistsError):
            create(self.instance, self.archive)
        restore(self.archive, self.dest)
        with self.assertRaises(FileExistsError):
            restore(self.archive, self.dest)
        self.assertEqual({p.name for p in self.dest.iterdir()}, {"store", "users.json"})
        self.assertEqual(stat.S_IMODE(self.archive.stat().st_mode), 0o600)
        for path in (self.dest, *self.dest.rglob("*")):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)
        access = Access("private", users=self.dest / "users.json")
        self.assertTrue(access.login("Test reviewer", "example password"))
        app = App(self.dest / "store")
        self.assertEqual(app.get(f"/api/runs/{report['run_id']}/audit"), report)
        replay = app.post("/api/deals/mastr/runs", {"mode": "replay", "replay": report["replay_case"]})
        self.assertEqual(replay["status"], "BLOCK")

    def test_running_server_blocks_backup_and_stopped_store_restores(self):
        policy = Access("private", users=self.instance / "users.json")
        with policy.open_store(self.store):
            with self.assertRaisesRegex(ValueError, "Stop the workspace"):
                create(self.instance, self.archive)
            self.assertFalse(self.archive.exists())
        create(self.instance, self.archive)
        restore(self.archive, self.dest)
        with policy.open_store(self.dest / "store"):
            self.assertTrue(policy.check_store(self.dest / "store"))

    def test_legacy_store_gets_a_private_regular_lock(self):
        lock = self.store / ".workspace.lock"
        self.assertFalse(lock.exists())
        create(self.instance, self.archive)
        self.assertTrue(lock.is_file())
        self.assertEqual(stat.S_IMODE(lock.stat().st_mode), 0o600)

    def test_rejects_path_and_digest_damage_without_publishing(self):
        create(self.instance, self.archive)
        for problem in ("hash", "traversal", "symlink"):
            with self.subTest(problem=problem):
                bad = self.base / (problem + ".zip")
                with zipfile.ZipFile(self.archive) as original, zipfile.ZipFile(bad, "w") as writer:
                    for item in original.infolist():
                        data = original.read(item)
                        if item.filename == "manifest.json" and problem == "hash":
                            inventory = json.loads(data)
                            inventory["files"]["users.json"] = "0" * 64
                            data = json.dumps(inventory).encode()
                        writer.writestr(item, data)
                    if problem == "traversal":
                        writer.writestr("store/../../escaped", b"x")
                    if problem == "symlink":
                        link = zipfile.ZipInfo("store/link")
                        link.external_attr = (stat.S_IFLNK | 0o777) << 16
                        writer.writestr(link, "../users.json")
                with self.assertRaises(ValueError):
                    restore(bad, self.dest)
                self.assertFalse(self.dest.exists())
                self.assertFalse((self.base / "escaped").exists())

    def test_rejects_source_symlink_and_nested_destination(self):
        with self.assertRaises(ValueError):
            create(self.instance, self.store / "recursive.zip")
        (self.store / "linked").symlink_to(self.instance / "users.json")
        with self.assertRaises(ValueError):
            create(self.instance, self.archive)
        self.assertFalse(self.archive.exists())
