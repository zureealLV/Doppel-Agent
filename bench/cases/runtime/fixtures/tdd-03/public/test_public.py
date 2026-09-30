from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from service import FilePatch


class PublicTests(unittest.TestCase):
    def test_fresh_patch_applies(self):
        with TemporaryDirectory() as root:
            target = Path(root) / "file.txt"
            target.write_text("base", encoding="utf-8")
            patch = FilePatch(root)
            patch.apply(patch.prepare("file.txt", "updated"))
            self.assertEqual(target.read_text(encoding="utf-8"), "updated")
