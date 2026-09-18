import unittest

from doppel_agent.tasks.manager import TaskManager
from support import workspace


class TaskManagerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = workspace()
        self.root = self.fixture.__enter__()
        self.addCleanup(lambda: self.fixture.__exit__(None, None, None))
        self.database = self.root / ".doppel-agent" / "tasks.sqlite3"
        self.manager = TaskManager(self.database)

    def test_dependencies_unlock_and_survive_restart(self):
        first = self.manager.create("run-a", "inspect", [])
        second = self.manager.create("run-a", "edit", [first])
        self.assertEqual([task["id"] for task in self.manager.ready("run-a")], [first])
        with self.assertRaises(ValueError):
            self.manager.transition("run-a", second, "start")
        self.manager.transition("run-a", first, "start")
        self.manager.transition("run-a", first, "complete", "inspected")
        reopened = TaskManager(self.database)
        self.assertEqual(reopened.get(first)["status"], "completed")
        self.assertEqual([task["id"] for task in reopened.ready("run-a")], [second])
        reopened.transition("run-a", second, "start")
        self.assertEqual(reopened.get(second)["attempts"], 1)

    def test_cycle_and_cross_run_rejected(self):
        first = self.manager.create("run-a", "first")
        second = self.manager.create("run-a", "second", [first])
        with self.assertRaisesRegex(ValueError, "cycle"):
            self.manager.set_dependencies(first, [second])
        outside = self.manager.create("run-b", "outside")
        with self.assertRaisesRegex(ValueError, "different run"):
            self.manager.set_dependencies(first, [outside])
        self.assertEqual(self.manager.list("run-a")[0]["dependencies"], [])

    def test_fail_retry_and_invalid_transition(self):
        task_id = self.manager.create("run-a", "build")
        self.manager.transition("run-a", task_id, "start")
        self.manager.transition("run-a", task_id, "fail", "compile error")
        self.manager.transition("run-a", task_id, "retry")
        self.manager.transition("run-a", task_id, "start")
        self.assertEqual(self.manager.get(task_id)["attempts"], 2)
        with self.assertRaisesRegex(ValueError, "invalid task transition"):
            self.manager.transition("run-a", task_id, "retry")
