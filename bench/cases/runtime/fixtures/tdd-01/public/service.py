"""Small in-memory run store. Each non-empty key identifies one creation."""


class RunStore:
    def __init__(self):
        self.rows = {}
        self.keys = {}
        self.next_id = 1

    def create(self, payload, idempotency_key=None):
        record = {"run_id": str(self.next_id), "payload": payload}
        self.next_id += 1
        self.rows[record["run_id"]] = record
        if idempotency_key:
            self.keys[idempotency_key] = record["run_id"]
        return record, True
