from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from service import FilePatch


class CandidateTests(unittest.TestCase):
    def test_stale_patch_raises(self):
        with TemporaryDirectory() as root:
            target = Path(root) / "file.txt"
            target.write_bytes(b"base")
            patch = FilePatch(root)
            proposal = patch.prepare("file.txt", "proposed")
            target.write_bytes(b"external")
            with self.assertRaises(ValueError):
                patch.apply(proposal)
