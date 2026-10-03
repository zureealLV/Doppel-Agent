"""Review keys are frozen external inputs, never provider/workspace material."""

from hashlib import sha256
import json
import shutil

import pytest

from bench.runtime_fixtures import FIXTURE_ROOT, freeze_review_inputs, materialize_review_case


def test_freeze_keeps_key_bytes_separate_and_immutable_after_disk_change(tmp_path):
    folder = tmp_path / "fixtures"
    for case_id in ("review-01", "review-02", "review-03", "review-04"):
        shutil.copytree(FIXTURE_ROOT / case_id, folder / case_id)
    public, keys = freeze_review_inputs(root=folder)
    before = sha256(keys["review-01"]).hexdigest()
    (folder / "review-01/answer_key.json").write_text("{}")
    assert sha256(keys["review-01"]).hexdigest() == before
    materialize_review_case("review-01", tmp_path / "agent", public_source=public["review-01"])
    assert {p.name for p in (tmp_path / "agent").iterdir()} == {"service.py"}
    assert json.loads(keys["review-01"])["findings"]
    with pytest.raises(TypeError):
        keys["review-01"] = b"forged"


@pytest.mark.parametrize("mutation", ["duplicate_id", "bool_line", "bad_signature", "wrong_case", "symlink"])
def test_review_freeze_rejects_ambiguous_or_outside_key_material(tmp_path, mutation):
    folder = tmp_path / "fixtures"
    for case_id in ("review-01", "review-02", "review-03", "review-04"):
        shutil.copytree(FIXTURE_ROOT / case_id, folder / case_id)
    path = folder / "review-01/answer_key.json"
    key = json.loads(path.read_bytes())
    if mutation == "duplicate_id":
        key["findings"].append(dict(key["findings"][0]))
    elif mutation == "bool_line":
        key["findings"][0]["line"] = True
    elif mutation == "bad_signature":
        key["findings"][0]["signature"] = "missing-frozen-source"
    elif mutation == "wrong_case":
        key["case_id"] = "review-02"
    else:
        target = tmp_path / "outside.json"
        shutil.copyfile(path, target)
        path.unlink()
        try:
            path.symlink_to(target)
        except OSError:
            pytest.skip("symlink creation unavailable on this host")
        with pytest.raises(ValueError):
            freeze_review_inputs(root=folder)
        return
    path.write_text(json.dumps(key))
    with pytest.raises(ValueError):
        freeze_review_inputs(root=folder)
