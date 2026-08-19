import os
import unittest

os.environ.setdefault("GEMINI_API_KEY", "test-key")

from opspilot.core.executor import approve_task, create_execution_plan, run_plan
from opspilot.core.workflow_store import workflow_store
from fake_graph import FakeGraph


class FakeGraphWorkflowTests(unittest.TestCase):
    def setUp(self):
        workflow_store.reset_for_tests()

    def test_dependent_user_license_and_draft_workflow_requires_preview_approval(self):
        graph = FakeGraph()
        plan = {
            "type": "plan",
            "tasks": [
                {"id": "users", "tool": "list_users", "parameters": {}},
                {"id": "profile", "tool": "get_user", "depends_on": ["users"], "parameters": {
                    "user": {"$ref": "users.users[0].displayName"},
                }},
                {"id": "licenses", "tool": "list_user_licenses", "depends_on": ["users"], "parameters": {
                    "user_id": {"$ref": "users.users[0].id"},
                    "display_name": {"$ref": "users.users[0].displayName"},
                }},
                {"id": "draft", "tool": "draft_email", "depends_on": ["profile", "licenses"], "parameters": {
                    "recipient": "ada@example.com",
                    "subject": "Engineering check-in",
                    "body": "Hello {{profile.data.displayName}}, your current licenses were reviewed.",
                }},
            ],
        }
        execution = create_execution_plan("fake-graph", plan)

        with graph.patched_tools():
            waiting = run_plan(execution["execution_id"])
            self.assertEqual(waiting["type"], "approval_required")
            self.assertEqual(waiting["tool"], "draft_email")
            self.assertEqual(waiting["preview"]["recipient"], "ada@example.com")
            self.assertIn("Ada Lovelace", waiting["preview"]["body"])
            self.assertEqual(graph.drafts, [])

            completed = approve_task(waiting["approval_id"])

        self.assertEqual(completed["type"], "completed")
        self.assertEqual(len(graph.drafts), 1)
        self.assertEqual(graph.drafts[0]["subject"], "Engineering check-in")

    def test_independent_fake_graph_failure_reports_partial_completion(self):
        graph = FakeGraph()
        graph.fail_paths.add("/subscribedSkus")
        execution = create_execution_plan("fake-graph", {
            "type": "plan",
            "tasks": [
                {"id": "users", "tool": "list_users", "parameters": {}},
                {"id": "licenses", "tool": "list_available_licenses", "parameters": {}},
            ],
        })

        with graph.patched_tools():
            result = run_plan(execution["execution_id"])

        self.assertEqual(result["type"], "partially_completed")
        self.assertTrue(result["results"]["users"]["success"])
        self.assertFalse(result["results"]["licenses"]["success"])
        self.assertEqual(result["results"]["licenses"]["error_code"], "http_error")


if __name__ == "__main__":
    unittest.main()
