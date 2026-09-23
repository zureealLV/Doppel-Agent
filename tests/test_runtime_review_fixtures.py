"""Review cases must have a reproducible flaw and a hidden answer key."""

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from bench.runtime_fixtures import REVIEW_CASE_IDS, materialize_review_case
from bench.runtime_matrix import RuntimeMatrix


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "bench/cases/runtime/fixtures"


def _service(case_id: str):
    source = FIXTURES / case_id / "public/service.py"
    spec = importlib.util.spec_from_file_location(f"runtime_{case_id.replace('-', '_')}", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RuntimeReviewFixtureTests(unittest.TestCase):
    def test_all_review_cases_have_hidden_precise_answer_keys(self):
        matrix = RuntimeMatrix.load(ROOT / "bench/cases/runtime/manifest.json")
        review_cases = [case for case in matrix.cases if case.category == "known_answer_review"]
        review_ids = {case.case_id for case in review_cases}
        self.assertEqual(matrix.protocol_version, "1.1")
        self.assertEqual(review_ids, set(REVIEW_CASE_IDS))
        self.assertEqual(len({case.prompt for case in review_cases}), 1)
        self.assertTrue(all("service.py" in case.prompt for case in review_cases))
        for case_id in REVIEW_CASE_IDS:
            fixture = FIXTURES / case_id
            key = json.loads((fixture / "answer_key.json").read_text(encoding="utf-8"))
            lines = (fixture / "public/service.py").read_text(encoding="utf-8").splitlines()
            self.assertEqual(key["case_id"], case_id)
            self.assertEqual(key["path"], "service.py")
            self.assertEqual(len(key["findings"]), 1)
            finding = key["findings"][0]
            self.assertIn(finding["signature"], lines[finding["line"] - 1])
            for field in ("trigger", "impact", "minimal_fix"):
                self.assertTrue(finding[field])

    def test_materialization_copies_only_public_source(self):
        with tempfile.TemporaryDirectory() as directory:
            for case_id in REVIEW_CASE_IDS:
                workspace = Path(directory) / case_id
                digest = materialize_review_case(case_id, workspace)
                self.assertEqual(len(digest), 64)
                self.assertEqual([p.relative_to(workspace).as_posix() for p in workspace.rglob("*")],
                                 ["service.py"])
                self.assertEqual(
                    (workspace / "service.py").read_bytes(),
                    (FIXTURES / case_id / "public/service.py").read_bytes(),
                )
            with self.assertRaises(ValueError):
                materialize_review_case("../review-01", Path(directory) / "bad")

    def test_review_01_cross_owner_read_is_real(self):
        service = _service("review-01")
        with sqlite3.connect(":memory:") as db:
            service.create_schema(db)
            db.executemany("INSERT INTO documents VALUES (?, ?, ?)",
                           [(1, "alice", "alice secret"), (2, "bob", "bob secret")])
            self.assertEqual(service.get_document(db, "alice", 2), "bob secret")

    def test_review_02_sql_injection_crosses_tenant_boundary(self):
        service = _service("review-02")
        with sqlite3.connect(":memory:") as db:
            service.create_schema(db)
            db.executemany("INSERT INTO documents VALUES (?, ?, ?)",
                           [(1, "alice", "alpha"), (2, "bob", "beta")])
            rows = service.search_documents(db, "alice", "%' OR 1=1 --")
            self.assertEqual({row[1] for row in rows}, {"alice", "bob"})

    def test_review_03_path_traversal_reads_sibling_secret(self):
        service = _service("review-03")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            attachment_root = root / "attachments"
            attachment_root.mkdir()
            (root / "secret.txt").write_text("private", encoding="utf-8")
            self.assertEqual(service.read_attachment(attachment_root, "../secret.txt"), b"private")

    def test_review_04_cancel_leaves_worker_process_alive(self):
        service = _service("review-04")
        process = service.start_worker()
        try:
            service.cancel_worker(process)
            self.assertIsNone(process.poll())
        finally:
            process.kill()
            process.wait(timeout=5)
            self.assertIsNotNone(process.poll())


if __name__ == "__main__":
    unittest.main()
