import tempfile
import unittest
from pathlib import Path

from actor import Actor
from workflow_store import WorkflowStore, expiry


def state(execution_id="execution"):
    return {
        "execution_id": execution_id,
        "session_id": "session",
        "actor": Actor(subject="alice"),
        "tasks": {},
        "results": {},
        "status": "running",
        "created_at": "now",
    }


class WorkflowStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "workflow.sqlite3"
        self.store = WorkflowStore(self.path)

    def tearDown(self):
        self.directory.cleanup()

    def test_execution_survives_new_store_instance(self):
        self.store.create_execution(state())
        restarted = WorkflowStore(self.path)
        recovered = restarted.get_execution("execution")
        self.assertEqual(recovered["actor"].subject, "alice")
        self.assertEqual(recovered["status"], "running")

    def test_idempotency_key_returns_original_execution(self):
        first = self.store.create_execution(state("first"), idempotency_key="request-1")
        second = self.store.create_execution(state("second"), idempotency_key="request-1")
        self.assertEqual(first["execution_id"], second["execution_id"])

    def test_approval_can_only_be_consumed_once_by_owner(self):
        self.store.create_execution(state())
        approval = {
            "approval_id": "approval",
            "execution_id": "execution",
            "task_id": "task",
            "actor_subject": "alice",
            "parameters": {"recipient": "a@example.com"},
            "expires_at": expiry(60),
        }
        self.store.create_approval(approval)
        self.assertIsNone(self.store.claim_approval("approval", "mallory", "consumed"))
        self.assertIsNotNone(self.store.claim_approval("approval", "alice", "consumed"))
        self.assertIsNone(self.store.claim_approval("approval", "alice", "consumed"))

    def test_expired_approval_cannot_be_claimed(self):
        self.store.create_execution(state())
        self.store.create_approval({
            "approval_id": "expired",
            "execution_id": "execution",
            "task_id": "task",
            "actor_subject": "alice",
            "parameters": {},
            "expires_at": "2000-01-01T00:00:00+00:00",
        })
        self.assertIsNone(self.store.claim_approval("expired", "alice", "consumed"))


if __name__ == "__main__":
    unittest.main()
