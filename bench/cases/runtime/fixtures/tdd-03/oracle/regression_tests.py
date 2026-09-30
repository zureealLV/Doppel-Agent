import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path.cwd()))
from service import FilePatch  # noqa: E402


class RegressionTests(unittest.TestCase):
    def test_unicode_patch(self):
        with TemporaryDirectory() as root:
            target = Path(root) / "file.txt"
            target.write_bytes(b"base")
            patch = FilePatch(root)
            patch.apply(patch.prepare("file.txt", "\u66f4\u65b0\n"))
            self.assertEqual(target.read_text(encoding="utf-8"), "\u66f4\u65b0\n")

    def test_unrelated_edit_does_not_block_patch(self):
        with TemporaryDirectory() as root:
            target = Path(root) / "file.txt"
            target.write_bytes(b"base")
            patch = FilePatch(root)
            proposal = patch.prepare("file.txt", "updated")
            (Path(root) / "other.txt").write_bytes(b"other")
            patch.apply(proposal)
            self.assertEqual(target.read_bytes(), b"updated")

    def test_escape_is_rejected_without_touching_external_file(self):
        with TemporaryDirectory() as parent:
            root = Path(parent) / "root"
            root.mkdir()
            outside = Path(parent) / "outside.txt"
            outside.write_bytes(b"untouched")
            with self.assertRaises(ValueError):
                FilePatch(root).prepare("../outside.txt", "bad")
            self.assertEqual(outside.read_bytes(), b"untouched")


if __name__ == "__main__":
    unittest.main()
