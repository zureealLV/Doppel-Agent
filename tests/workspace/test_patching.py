import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from doppel_agent.workspace.patching import (
    PatchConflictError,
    PatchProposal,
    PatchService,
)


class PatchServiceTests(unittest.TestCase):
    def test_prepare_is_side_effect_free_and_apply_writes_reviewed_diff(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "example.py"
            target.write_text("value = 1\n", encoding="utf-8")
            service = PatchService(root)

            proposal = service.prepare([{"path": "example.py", "content": "value = 2\n"}])

            self.assertEqual(target.read_text(encoding="utf-8"), "value = 1\n")
            self.assertIn("-value = 1", proposal.unified_diff)
            self.assertIn("+value = 2", proposal.unified_diff)
            result = service.apply(proposal)
            self.assertEqual(target.read_text(encoding="utf-8"), "value = 2\n")
            self.assertEqual(result.changed_paths, ("example.py",))

    def test_apply_rejects_stale_base_without_overwriting_external_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "example.py"
            target.write_text("value = 1\n", encoding="utf-8")
            service = PatchService(root)
            proposal = service.prepare([{"path": "example.py", "content": "value = 2\n"}])
            target.write_text("value = 99\n", encoding="utf-8")

            with self.assertRaisesRegex(PatchConflictError, "stale patch base"):
                service.apply(proposal)

            self.assertEqual(target.read_text(encoding="utf-8"), "value = 99\n")

    def test_multi_file_apply_rolls_back_every_replaced_file_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.txt"
            second = root / "second.txt"
            first.write_text("first-old", encoding="utf-8")
            second.write_text("second-old", encoding="utf-8")
            service = PatchService(root)
            proposal = service.prepare(
                [
                    {"path": "first.txt", "content": "first-new"},
                    {"path": "second.txt", "content": "second-new"},
                ]
            )
            from doppel_agent.workspace import patching

            real_replace = patching.os.replace
            replace_count = 0

            def fail_second_replace(source, target):
                nonlocal replace_count
                replace_count += 1
                if replace_count == 2:
                    raise OSError("simulated second-file failure")
                return real_replace(source, target)

            with patch.object(patching.os, "replace", side_effect=fail_second_replace):
                with self.assertRaisesRegex(OSError, "second-file failure"):
                    service.apply(proposal)

            self.assertEqual(first.read_text(encoding="utf-8"), "first-old")
            self.assertEqual(second.read_text(encoding="utf-8"), "second-old")
            self.assertEqual(list(root.glob("*.tmp")), [])

    def test_apply_rejects_a_tampered_review_diff(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "example.py"
            target.write_text("value = 1\n", encoding="utf-8")
            service = PatchService(root)
            proposal = service.prepare([{"path": "example.py", "content": "value = 2\n"}])
            tampered = PatchProposal(proposal.patch_id, proposal.changes, "looks harmless")

            with self.assertRaisesRegex(ValueError, "integrity check failed"):
                service.apply(tampered)

            self.assertEqual(target.read_text(encoding="utf-8"), "value = 1\n")


if __name__ == "__main__":
    unittest.main()
