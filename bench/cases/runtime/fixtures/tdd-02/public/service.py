"""Bounded FIFO: attempts = accepted + rejected; outstanding = queued."""


class BoundedQueue:
    def __init__(self, capacity):
        if capacity < 0:
            raise ValueError("capacity must be non-negative")
        self.capacity = capacity
        self.queued = []
        self.attempts = self.accepted = self.rejected = self.completed = 0

    def submit(self, value):
        self.attempts += 1
        self.accepted += 1
        if len(self.queued) >= self.capacity:
            self.rejected += 1
            return False
        self.queued.append(value)
        return True

    def finish_next(self):
        value = self.queued.pop(0)
        self.completed += 1
        return value

    @property
    def outstanding(self):
        return self.accepted - self.completed
