"""Focused contracts for registered tools.

These tests use mocked Graph boundaries.  They intentionally cover distinct
request shapes and failure branches instead of duplicating GraphClient tests.
"""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("GEMINI_API_KEY", "test-key")

from graph.client import GraphError
from tool_spec import TOOL_SPECS, get_tool_spec
from tools import licenses, mail, users


class ToolSpecContractTests(unittest.TestCase):
    def test_registered_tools_enforce_required_inputs_and_approval_policy(self):
        """Every registration has the intended input and approval contract."""
        required = {
            "create_user", "send_email", "summarize_thread", "generate_reply",
            "summarize_email", "draft_email", "reply_email", "get_email",
            "get_user", "get_account_report", "assign_license", "disable_user", "enable_user",
            "reset_password", "revoke_sessions", "delete_user",
            "list_user_licenses", "search_emails",
        }
        writes = {
            "create_user", "send_email", "reply_email", "assign_license",
            "disable_user", "enable_user", "reset_password", "revoke_sessions",
            "delete_user", "draft_email",
        }

        self.assertEqual(len(TOOL_SPECS), 22)
        self.assertEqual(
            {name for name, spec in TOOL_SPECS.items() if spec.requires_approval},
            writes,
        )
        for name, spec in TOOL_SPECS.items():
            with self.subTest(tool=name):
                if name in required:
                    with self.assertRaises(ValueError):
                        spec.validate_input({})
                else:
                    spec.validate_input({})


class MailToolContracts(unittest.TestCase):
    @patch("tools.mail.graph_get_collection")
    def test_mail_listing_and_search_construct_safe_graph_queries(self, graph_get_collection):
        graph_get_collection.return_value = [{
            "id": "message-1", "subject": "Status", "isRead": False,
            "from": {"emailAddress": {"address": "sender@example.com"}},
        }]

        unread = mail.list_unread_emails({"mailbox": "a/b@example.com", "count": 3})
        unread_args, unread_kwargs = graph_get_collection.call_args
        self.assertEqual(unread_args[0], "/users/a%2Fb@example.com/messages")
        self.assertEqual(unread_kwargs["params"]["$filter"], "isRead eq false")
        self.assertEqual(unread_kwargs["limit"], 3)
        self.assertEqual(
            get_tool_spec("list_unread_emails").normalize_result(unread)["public_summary"],
            "Found 1 unread email(s).",
        )

        search = mail.search_emails({"query": "quarterly report", "count": 2})
        _, search_kwargs = graph_get_collection.call_args
        self.assertEqual(search_kwargs["params"]["$search"], '"quarterly report"')
        self.assertEqual(search_kwargs["headers"], {"ConsistencyLevel": "eventual"})
        self.assertEqual(search_kwargs["limit"], 2)
        self.assertEqual(search["message"], "Found 1 email(s) matching the search.")

    @patch("tools.mail.graph_post")
    def test_mail_write_tools_construct_distinct_graph_payloads(self, graph_post):
        graph_post.return_value = {"id": "draft-1"}

        draft = mail.draft_email({"recipient": "to@example.com", "subject": "Hello", "body": "Body"})
        draft_path, draft_body = graph_post.call_args.args
        self.assertEqual(draft_path, "/users/studio1@nebulousdesign.com/messages")
        self.assertEqual(draft_body["toRecipients"][0]["emailAddress"]["address"], "to@example.com")
        self.assertEqual(draft["message"], "Email draft created for to@example.com.")

        mail.reply_email({"email_id": "id/with?chars", "body": "Thanks"})
        reply_path, reply_body = graph_post.call_args.args
        self.assertEqual(reply_path, "/users/studio1@nebulousdesign.com/messages/id%2Fwith%3Fchars/reply")
        self.assertEqual(reply_body["message"]["body"]["content"], "Thanks")

        sent = mail.send_email({"recipient": "to@example.com", "subject": "Hello", "body": "Body"})
        send_path, send_body = graph_post.call_args.args
        self.assertEqual(send_path, "/users/studio1@nebulousdesign.com/sendMail")
        self.assertTrue(send_body["saveToSentItems"])
        self.assertEqual(sent["message"], "Email sent to to@example.com.")

    def test_mail_tool_validation_rejects_invalid_counts_and_searches(self):
        with self.assertRaisesRegex(ValueError, "positive integer"):
            mail.list_recent_emails({"count": 0})
        with self.assertRaisesRegex(ValueError, "non-empty string"):
            mail.search_emails({"query": "   "})


class UserAndLicenseToolContracts(unittest.TestCase):
    @patch("tools.users.graph_delete")
    @patch("tools.users.graph_post")
    @patch("tools.users.update_user")
    @patch("tools.users.find_users", return_value=[{"id": "id/1", "displayName": "Ada Lovelace"}])
    def test_user_mutations_resolve_one_user_and_construct_graph_requests(
        self, _find_users, update_user, graph_post, graph_delete
    ):
        disabled = users.disable_user({"user": "Ada Lovelace"})
        self.assertEqual(update_user.call_args.args, ("id/1", {"accountEnabled": False}))
        self.assertEqual(disabled["message"], "Ada Lovelace has been disabled.")

        enabled = users.enable_user({"user": "Ada Lovelace"})
        self.assertEqual(update_user.call_args.args, ("id/1", {"accountEnabled": True}))
        self.assertEqual(enabled["message"], "Ada Lovelace has been enabled.")

        revoked = users.revoke_sessions({"user": "Ada Lovelace"})
        self.assertEqual(graph_post.call_args.args, ("/users/id%2F1/revokeSignInSessions", {}))
        self.assertEqual(revoked["message"], "All sign-in sessions revoked for Ada Lovelace.")

        deleted = users.delete_user({"user": "Ada Lovelace"})
        self.assertEqual(graph_delete.call_args.args, ("/users/id%2F1",))
        self.assertEqual(deleted["message"], "Ada Lovelace has been deleted.")

    @patch("tools.users.find_users", return_value=[])
    def test_user_mutations_normalize_a_missing_target_without_graph_call(self, _find_users):
        for tool in (users.disable_user, users.enable_user, users.revoke_sessions, users.delete_user):
            with self.subTest(tool=tool.__name__):
                result = get_tool_spec(tool.__name__).normalize_result(tool({"user": "Missing"}))
                self.assertFalse(result["success"])
                self.assertEqual(result["error_code"], "tool_failed")
                self.assertIn("No user found", result["public_summary"])

    @patch("tools.licenses.graph_update_license")
    def test_assign_license_uses_exact_user_id_and_normalizes_graph_failure(self, graph_update_license):
        success = licenses.assign_license({"user_id": "id/with?chars", "display_name": "Ada", "license": "Flow Free"})
        self.assertTrue(success["success"])
        self.assertEqual(graph_update_license.call_args.args[0], "id/with?chars")
        self.assertEqual(success["message"], "Flow Free assigned to Ada.")

        graph_update_license.side_effect = GraphError("http_error", "Request failed", retryable=True)
        failed = get_tool_spec("assign_license").normalize_result(
            licenses.assign_license({"user_id": "id-1", "license": "Flow Free"})
        )
        self.assertFalse(failed["success"])
        self.assertEqual(failed["error_code"], "http_error")
        self.assertTrue(failed["retryable"])
        self.assertIn("Microsoft Graph failed", failed["public_summary"])


if __name__ == "__main__":
    unittest.main()
