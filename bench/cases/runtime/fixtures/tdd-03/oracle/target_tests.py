import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path.cwd()))
from service import FilePatch  # noqa: E402


class TargetTests(unittest.TestCase):
    def test_external_edit_is_rejected_without_overwrite(self):
        with TemporaryDirectory() as root:
            target = Path(root) / "file.txt"
            target.write_bytes(b"base")
            patch = FilePatch(root)
            proposal = patch.prepare("file.txt", "proposed")
            target.write_bytes(b"external edit\r\n")
            with self.assertRaises(ValueError):
                patch.apply(proposal)
            self.assertEqual(target.read_bytes(), b"external edit\r\n")

    def test_deleted_base_is_not_recreated(self):
        with TemporaryDirectory() as root:
            target = Path(root) / "file.txt"
            target.write_bytes(b"base")
            patch = FilePatch(root)
            proposal = patch.prepare("file.txt", "proposed")
            target.unlink()
            with self.assertRaises(ValueError):
                patch.apply(proposal)
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
