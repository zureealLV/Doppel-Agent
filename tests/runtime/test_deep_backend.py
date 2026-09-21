import tempfile
import unittest
from pathlib import Path

from doppel_agent.runtime.deep_backend import DoppelBackend


class DeepBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
        (self.root / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
        (self.root / ".git").mkdir()
        (self.root / ".git" / "config").write_text("private", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_filters_secret_and_agent_state_paths(self):
        backend = DoppelBackend(self.root)
        listing = backend.ls("/")
        self.assertEqual([item["path"] for item in listing.entries], ["/src/"])
        self.assertIn("Permission denied", backend.read("/.env").error)
        self.assertIn("Permission denied", backend.read("/.git/config").error)
        self.assertEqual(backend.read("/src/app.py").file_data["content"], "print('ok')\n")

    def test_blocks_escape_and_requires_write_grant(self):
        readonly = DoppelBackend(self.root)
        self.assertIsNotNone(readonly.write("/created.txt", "x").error)
        self.assertIsNotNone(readonly.read("/../outside.txt").error)

        writable = DoppelBackend(self.root, allow_write=True)
        self.assertIsNone(writable.write("/created.txt", "x").error)
        self.assertEqual((self.root / "created.txt").read_text(encoding="utf-8"), "x")

    def test_symlink_cannot_alias_protected_directory(self):
        link = self.root / "alias"
        try:
            link.symlink_to(self.root / ".git", target_is_directory=True)
        except OSError:
            self.skipTest("symlink creation is unavailable")
        backend = DoppelBackend(self.root)
        self.assertIn("Permission denied", backend.read("/alias/config").error)
