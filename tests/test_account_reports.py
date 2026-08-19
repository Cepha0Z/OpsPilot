import os
import unittest

os.environ.setdefault("GEMINI_API_KEY", "test-key")

from fake_graph import FakeGraph
from opspilot.core.agent import final_response
from opspilot.core.planner import parse_plan
from opspilot.core.tool_spec import get_tool_spec
from opspilot.tools.users import get_account_report


class AccountReportTests(unittest.TestCase):
    def test_single_user_report_includes_profile_and_license_summary(self):
        graph = FakeGraph()
        with graph.patched_tools():
            result = get_tool_spec("get_account_report").normalize_result(
                get_account_report({"user": "Ada Lovelace"})
            )

        self.assertTrue(result["success"])
        self.assertIn("Account report for Ada Lovelace", result["public_summary"])
        self.assertIn("Engineering", result["public_summary"])
        self.assertIn("Engineer", result["public_summary"])
        self.assertIn("Enabled", result["public_summary"])
        self.assertIn("Flow Free", result["public_summary"])
        self.assertEqual(result["data"]["reports"][0]["email"], "ada@nebulous.example")

        response = final_response({"type": "completed", "results": {"report": result}})
        self.assertIn("Account report for Ada Lovelace", response["message"])

    def test_department_report_lists_multiple_users_concisely(self):
        graph = FakeGraph()
        with graph.patched_tools():
            result = get_tool_spec("get_account_report").normalize_result(
                get_account_report({"department": "IT"})
            )

        self.assertTrue(result["success"])
        self.assertIn("Account report for 2 users in IT", result["public_summary"])
        self.assertIn("Linus Torvalds", result["public_summary"])
        self.assertIn("Katherine Johnson", result["public_summary"])
        self.assertIn("disabled", result["public_summary"])
        self.assertEqual(len(result["data"]["reports"]), 2)

    def test_account_report_requires_exactly_one_supported_selector(self):
        spec = get_tool_spec("get_account_report")
        with self.assertRaisesRegex(ValueError, "require a user or department"):
            spec.validate_input({})
        with self.assertRaisesRegex(ValueError, "either a user or department"):
            spec.validate_input({"user": "Ada", "department": "IT"})

    def test_planner_contract_marks_account_reports_as_read_only(self):
        plan = parse_plan('{"type":"plan","tasks":[{"id":"report","tool":"get_account_report","parameters":{"department":"IT"}}]}')
        self.assertFalse(plan["tasks"][0]["requires_approval"])


if __name__ == "__main__":
    unittest.main()
