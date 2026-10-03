"""One frozen native permission/eligibility contract per scripted audit batch."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from bench.runtime_freeze import canonical_json, freeze_json
from bench.runtime_matrix import CAPABILITY_BOUNDARIES, RuntimeMatrix


def native_approval_decisions(events):
    """Record actual service decisions, not provider text or approval counters.

    Edited source/arguments stay out of this summary; the event hash binds the
    complete durable row for the trusted local evidence chain.
    """
    decisions, ids, sequences = [], set(), set()
    for event in events:
        if event.get("type") != "approval.decided":
            continue
        payload = event.get("payload")
        decision = payload.get("decision") if isinstance(payload, dict) else None
        interrupt_id = payload.get("interrupt_id") if isinstance(payload, dict) else None
        action = decision.get("action") if isinstance(decision, dict) else None
        seq = event.get("seq")
        if not isinstance(interrupt_id, str) or not interrupt_id or action not in {"approve", "reject", "edit"} or (
            type(seq) is not int or seq < 1 or seq in sequences or interrupt_id in ids
        ):
            raise ValueError("native approval events must have valid distinct IDs, actions and sequences")
        ids.add(interrupt_id)
        sequences.add(seq)
        decisions.append({"source": "service_event", "event_seq": seq,
                          "event_sha256": sha256(canonical_json(event)).hexdigest(),
                          "interrupt_id": interrupt_id, "action": action})
    return decisions


@dataclass(frozen=True)
class NativeTaskContract:
    matrix: RuntimeMatrix
    capabilities: object

    def __post_init__(self):
        object.__setattr__(self, "capabilities", freeze_json(self.capabilities))

    @classmethod
    def load(cls, matrix=None, *, path=None, root=None):
        folder = Path(root) / "bench/cases/runtime" if root is not None else Path(__file__).parent / "cases/runtime"
        matrix = matrix if matrix is not None else RuntimeMatrix.load(folder / "manifest.json")
        path = path if path is not None else folder / "capabilities.json"
        raw = Path(path).read_bytes()
        return cls(matrix, {boundary: matrix.capability_document(path, boundary=boundary, _raw=raw) for boundary in CAPABILITY_BOUNDARIES})

    def case(self, fixture, mode, *, boundary="run_service"):
        case = next((row for row in self.matrix.cases if row.case_id == fixture.case_id), None)
        if case is None or case.category != fixture.category or f"{fixture.case_id}:{mode}:1" not in self.capabilities[boundary]["supported_run_keys"]:
            raise ValueError("fixture is unsupported by its frozen native contract")
        return case
