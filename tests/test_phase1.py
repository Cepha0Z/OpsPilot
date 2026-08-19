import os
import json
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("GEMINI_API_KEY", "test-key")

from opspilot.core.actor import Actor
import opspilot.core.executor as executor_module
from opspilot.core.agent import (
    build_execution_report,
    final_response,
    run_agent,
    unsupported_capability_response,
)
from opspilot.core.audit_log import redact
from opspilot.core.executor import (
    approve_task,
    create_approval,
    create_execution_plan,
    determine_execution_outcome,
    reject_task,
    run_plan,
)
from opspilot.core.workflow_store import workflow_store
from opspilot.core.planner import parse_plan, plan_diagnostic, plan_request, validate_tasks
from opspilot.core.models import CommandResult, sanitize_public_text
from opspilot.integrations.graph.client import GraphError
from opspilot.core.tool_spec import TOOL_SPECS
from opspilot.core.tool_spec import get_tool_spec
from opspilot.tools.licenses import list_user_licenses
from opspilot.tools.ai import summarize_email
import opspilot.tools.users as users_module
from opspilot.services.llm.client import ask


class Phase1ContractTests(unittest.TestCase):
    def setUp(self):
        workflow_store.reset_for_tests()

    def test_remove_license_is_not_registered_or_plannable(self):
        self.assertNotIn("remove_license", TOOL_SPECS)
        with self.assertRaisesRegex(ValueError, "Unknown tool"):
            validate_tasks([
                {"id": "remove", "tool": "remove_license", "parameters": {}}
            ])

    @patch("opspilot.core.agent.plan_request")
    def test_acknowledgement_does_not_trigger_a_workflow(self, plan_request):
        response = run_agent("thanks next", session_id="acknowledgement-session")

        self.assertEqual(response["type"], "final")
        self.assertEqual(response["message"], "You’re welcome. What would you like to do next?")
        plan_request.assert_not_called()

    def test_plan_diagnostics_are_specific_without_echoing_plan_content(self):
        cases = [
            (ValueError("Missing required parameter 'recipient'."), "missing_required_field"),
            (ValueError("Unknown tool in plan: exfiltrate_secrets"), "unknown_tool"),
            (ValueError("Task 'email' references 'users' without declaring it as a dependency."), "invalid_dependency"),
            (ValueError("Circular dependency detected."), "circular_dependency"),
            (ValueError("Parameters for task must be an object."), "invalid_plan"),
        ]
        for error, expected_code in cases:
            with self.subTest(error=str(error)):
                code, message = plan_diagnostic(error)
                self.assertEqual(code, expected_code)
                self.assertNotIn("exfiltrate_secrets", message)

    @patch("opspilot.core.agent.plan_request")
    def test_invalid_planner_output_returns_a_safe_diagnostic(self, plan_request):
        plan_request.return_value = json.dumps({
            "type": "plan",
            "tasks": [{"id": "bad", "tool": "not_registered", "parameters": {}}],
        })

        response = run_agent("do something", session_id="invalid-plan-session")

        self.assertEqual(response["type"], "error")
        self.assertEqual(response["error_code"], "unknown_tool")
        self.assertIn("does not support", response["message"])
        self.assertNotIn("not_registered", response["message"])

    @patch("opspilot.core.agent.plan_request")
    def test_unsupported_capabilities_are_rejected_before_planning(self, plan_request):
        response = run_agent(
            "Find users who haven't received an email from me in the last 30 days.",
            session_id="unsupported-session",
        )

        self.assertEqual(response["error_code"], "unsupported_capability")
        self.assertIn("Sent Items", response["message"])
        plan_request.assert_not_called()

    def test_unsupported_capability_messages_cover_dynamic_and_comparison_workflows(self):
        cases = [
            ("For each user, draft a check-in email.", "dynamically"),
            ("Find users who are missing licenses.", "compare two Graph datasets"),
            ("Show the most recent users.", "rank users"),
        ]
        for request, expected_text in cases:
            with self.subTest(request=request):
                response = unsupported_capability_response(request, "unsupported-session")
                self.assertEqual(response["error_code"], "unsupported_capability")
                self.assertIn(expected_text, response["message"])

    def test_create_account_without_credential_email_is_a_valid_plan(self):
        plan = parse_plan(json.dumps({
            "type": "plan",
            "tasks": [{
                "id": "create_rat_joe",
                "tool": "create_user",
                "depends_on": [],
                "parameters": {
                    "first_name": "Rat",
                    "last_name": "Joe",
                    "department": "IT",
                },
                "requires_approval": True,
            }],
        }))

        self.assertEqual(plan["tasks"][0]["parameters"]["department"], "IT")
        self.assertTrue(plan["tasks"][0]["requires_approval"])

    def test_create_account_with_credential_email_preserves_personal_email(self):
        plan = parse_plan(json.dumps({
            "type": "plan",
            "tasks": [
                {
                    "id": "create_ada",
                    "tool": "create_user",
                    "depends_on": [],
                    "parameters": {
                        "first_name": "Ada",
                        "last_name": "Lovelace",
                        "department": "Engineering",
                        "personal_email": "ada@example.com",
                    },
                    "requires_approval": True,
                },
                {
                    "id": "send_credentials",
                    "tool": "send_email",
                    "depends_on": ["create_ada"],
                    "parameters": {
                        "recipient": "ada@example.com",
                        "subject": "Welcome",
                        "body": "Username: {{create_ada.company_email}}\nTemporary password: {{create_ada.temporary_password}}",
                    },
                    "requires_approval": True,
                },
            ],
        }))

        self.assertEqual(plan["tasks"][0]["parameters"]["personal_email"], "ada@example.com")
        self.assertEqual(plan["tasks"][1]["parameters"]["recipient"], "ada@example.com")

    def test_new_account_license_and_credential_email_plan_is_valid(self):
        """A common three-action request must retain every required input."""
        plan = parse_plan(json.dumps({
            "type": "plan",
            "tasks": [
                {
                    "id": "create_pri",
                    "tool": "create_user",
                    "parameters": {
                        "first_name": "Pri",
                        "last_name": "Shah",
                        "department": "Marketing",
                        "personal_email": "pri@example.com",
                    },
                },
                {
                    "id": "assign_pri_flow_free",
                    "tool": "assign_license",
                    "depends_on": ["create_pri"],
                    "parameters": {
                        "user_id": {"$ref": "create_pri.user_id"},
                        "license": "Flow Free",
                    },
                },
                {
                    "id": "send_pri_credentials",
                    "tool": "send_email",
                    "depends_on": ["create_pri"],
                    "parameters": {
                        "recipient": "pri@example.com",
                        "subject": "Your Microsoft 365 account",
                        "body": (
                            "Welcome.\\n\\nUsername: {{create_pri.company_email}}\\n"
                            "Temporary password: {{create_pri.temporary_password}}"
                        ),
                    },
                },
            ],
        }))

        tasks = {task["id"]: task for task in plan["tasks"]}
        self.assertTrue(all(task["requires_approval"] for task in tasks.values()))
        self.assertEqual(
            tasks["assign_pri_flow_free"]["parameters"]["user_id"],
            {"$ref": "create_pri.user_id"},
        )
        self.assertIn("{{create_pri.company_email}}", tasks["send_pri_credentials"]["parameters"]["body"])
        self.assertIn("{{create_pri.temporary_password}}", tasks["send_pri_credentials"]["parameters"]["body"])

    @patch("opspilot.core.planner.ask")
    def test_planner_repairs_an_incomplete_credential_email_plan(self, ask):
        incomplete = {
            "type": "plan",
            "tasks": [{
                "id": "send_credentials",
                "tool": "send_email",
                "parameters": {"recipient": "pri@example.com"},
            }],
        }
        repaired = {
            "type": "plan",
            "tasks": [{
                "id": "send_credentials",
                "tool": "send_email",
                "parameters": {
                    "recipient": "pri@example.com",
                    "subject": "Your Microsoft 365 account",
                    "body": "Welcome.",
                },
            }],
        }
        ask.side_effect = [json.dumps(incomplete), json.dumps(repaired)]

        response = plan_request("Email Pri her login credentials.")

        self.assertEqual(json.loads(response), repaired)
        self.assertEqual(ask.call_count, 2)
        self.assertIn("send_email task requires recipient, subject, and body", ask.call_args.args[0])

    def test_named_user_email_draft_uses_profile_email_with_a_dependency(self):
        plan = parse_plan(json.dumps({
            "type": "plan",
            "tasks": [
                {
                    "id": "get_cepha",
                    "tool": "get_user",
                    "depends_on": [],
                    "parameters": {"user": "Cepha G"},
                },
                {
                    "id": "draft_email_to_cepha",
                    "tool": "draft_email",
                    "depends_on": ["get_cepha"],
                    "parameters": {
                        "recipient": "{{get_cepha.data.userPrincipalName}}",
                        "subject": "Greeting",
                        "body": "Hello Cepha G.",
                    },
                },
            ],
        }))

        draft = plan["tasks"][1]
        self.assertEqual(draft["parameters"]["recipient"], "{{get_cepha.data.userPrincipalName}}")
        self.assertTrue(draft["requires_approval"])

    def test_reference_must_be_a_dependency(self):
        tasks = [
            {
                "id": "create",
                "tool": "create_user",
                "parameters": {
                    "first_name": "Ada",
                    "last_name": "Lovelace",
                    "department": "Engineering",
                    "personal_email": "ada@example.com",
                },
            },
            {
                "id": "license",
                "tool": "assign_license",
                "parameters": {
                    "user_id": {"$ref": "create.user_id"},
                    "license": "flow free",
                },
            },
        ]
        with self.assertRaisesRegex(ValueError, "without declaring"):
            validate_tasks(tasks)

        tasks[1]["depends_on"] = ["create"]
        validate_tasks(tasks)
        self.assertTrue(tasks[1]["requires_approval"])

    def test_reference_unknown_output_field_is_rejected(self):
        tasks = [
            {"id": "users", "tool": "list_users", "parameters": {}},
            {
                "id": "email",
                "tool": "send_email",
                "depends_on": ["users"],
                "parameters": {
                    "recipient": "{{users.not_an_output}}",
                    "subject": "Hello",
                    "body": "Body",
                },
            },
        ]
        with self.assertRaisesRegex(ValueError, "not declared"):
            validate_tasks(tasks)

    def test_secret_redaction(self):
        value = redact({"password": "secret", "body": "private", "safe": "kept"})
        self.assertEqual(value["password"], "[REDACTED]")
        self.assertEqual(value["body"], "[REDACTED]")
        self.assertEqual(value["safe"], "kept")

    @patch("opspilot.services.llm.client.time.sleep")
    @patch("opspilot.services.llm.client.client.models.generate_content")
    def test_llm_retries_transient_timeout(self, generate_content, _sleep):
        generate_content.side_effect = [
            RuntimeError("request timed out"),
            type("Response", (), {"text": "planned response"})(),
        ]

        self.assertEqual(ask("test prompt", response_json=True), "planned response")
        self.assertEqual(generate_content.call_count, 2)
        self.assertEqual(generate_content.call_args.kwargs["config"]["response_mime_type"], "application/json")

    def test_temporary_password_is_private_not_public(self):
        result = CommandResult.from_legacy({
            "success": True,
            "temporary_password": "one-time-secret",
            "company_email": "ada@example.com",
        }).to_dict()
        self.assertNotIn("temporary_password", result["data"])
        self.assertEqual(result["private_data"]["temporary_password"], "one-time-secret")

    @patch("opspilot.tools.users.graph_post")
    @patch("opspilot.tools.users.NEW_USER_TEMPORARY_PASSWORD", "ConfiguredTestPassword1!")
    def test_create_user_uses_the_configured_password_for_graph_and_email_reference(self, graph_post):
        graph_post.return_value = {"id": "user-id", "displayName": "Ada Lovelace"}
        result = users_module.create_user({
            "first_name": "Ada",
            "last_name": "Lovelace",
            "department": "Engineering",
            "personal_email": "ada@example.com",
        })
        graph_payload = graph_post.call_args.args[1]

        self.assertEqual(
            graph_payload["passwordProfile"]["password"],
            result["temporary_password"],
        )
        self.assertTrue(graph_payload["passwordProfile"]["forceChangePasswordNextSignIn"])

    @patch("opspilot.tools.users.graph_post")
    @patch("opspilot.tools.users.NEW_USER_TEMPORARY_PASSWORD", "ConfiguredTestPassword1!")
    def test_create_user_handles_missing_personal_email(self, graph_post):
        graph_post.return_value = {"id": "user-id", "displayName": "Rat Joe"}

        result = users_module.create_user({
            "first_name": "Rat",
            "last_name": "Joe",
            "department": "IT",
        })

        self.assertIsNone(result["personal_email"])

    @patch("opspilot.tools.users.NEW_USER_TEMPORARY_PASSWORD", None)
    def test_create_user_fails_when_the_configured_password_is_missing(self):
        with self.assertRaisesRegex(ValueError, "temporary password is not configured"):
            users_module.create_user({
                "first_name": "Ada",
                "last_name": "Lovelace",
                "department": "Engineering",
                "personal_email": "ada@example.com",
            })

    @patch("opspilot.tools.users.update_user")
    @patch("opspilot.tools.users.find_users", return_value=[{"id": "user-id", "displayName": "Ada Lovelace"}])
    @patch("opspilot.tools.users.NEW_USER_TEMPORARY_PASSWORD", "ConfiguredTestPassword1!")
    def test_reset_uses_configured_password_and_keeps_it_private(
        self, _find_users, update_user
    ):
        result = users_module.reset_password({"user": "Ada Lovelace"})
        graph_payload = update_user.call_args.args[1]
        normalized = CommandResult.from_legacy(result).to_dict()

        self.assertEqual(
            graph_payload["passwordProfile"]["password"],
            result["temporary_password"],
        )
        self.assertTrue(graph_payload["passwordProfile"]["forceChangePasswordNextSignIn"])
        self.assertNotIn("temporary_password", normalized["data"])
        self.assertEqual(normalized["private_data"]["temporary_password"], result["temporary_password"])

    def test_reset_password_can_supply_a_dependent_credential_email(self):
        validate_tasks([
            {"id": "reset", "tool": "reset_password", "parameters": {"user": "Ada Lovelace"}},
            {"id": "send", "tool": "send_email", "depends_on": ["reset"], "parameters": {
                "recipient": "ada@example.com",
                "subject": "Password reset",
                "body": "Temporary password: {{reset.temporary_password}}",
            }},
        ])

    def test_credential_approval_redacts_the_api_preview_but_keeps_execution_value(self):
        state = create_execution_plan("session", {
            "type": "plan",
            "tasks": [
                {"id": "create", "tool": "create_user", "parameters": {}},
                {"id": "send", "tool": "send_email", "depends_on": ["create"], "parameters": {
                    "recipient": "ada@example.com",
                    "subject": "Welcome",
                    "body": "Temporary password: {{create.temporary_password}}",
                }},
            ],
        })
        state["results"]["create"] = CommandResult.from_legacy({
            "success": True,
            "temporary_password": "ConfiguredTestPassword1!",
        }).to_dict()

        response = create_approval(state, state["tasks"]["send"])
        stored = workflow_store.get_approval(response["approval_id"])

        self.assertEqual(response["parameters"]["body"], "[REDACTED]")
        self.assertEqual(response["approval_summary"]["parameters"]["body"], "[REDACTED]")
        self.assertIn("ConfiguredTestPassword1!", stored["parameters"]["body"])

    def test_draft_approval_exposes_a_safe_preview_before_creating_the_draft(self):
        state = create_execution_plan("session", {
            "type": "plan",
            "tasks": [{"id": "draft", "tool": "draft_email", "parameters": {
                "recipient": "ada@example.com",
                "subject": "Welcome",
                "body": "Temporary password: one-time-secret",
            }}],
        })

        response = create_approval(state, state["tasks"]["draft"])

        self.assertEqual(response["parameters"]["body"], "[REDACTED]")
        self.assertEqual(response["preview"]["recipient"], "ada@example.com")
        self.assertIn("Temporary password [REDACTED]", response["preview"]["body"])
        self.assertNotIn("one-time-secret", response["preview"]["body"])

    def test_rejection_reports_cancelled_not_completed(self):
        actor = Actor(subject="alice")
        state = create_execution_plan("session", {
            "type": "plan",
            "tasks": [{"id": "send", "tool": "send_email", "parameters": {
                "recipient": "a@example.com", "subject": "Subject", "body": "Body"
            }}],
        }, actor=actor)
        approval = run_plan(state["execution_id"])
        self.assertEqual(approval["type"], "approval_required")
        outcome = reject_task(approval["approval_id"], actor=actor)
        self.assertEqual(outcome["type"], "cancelled")

    def test_wrong_actor_cannot_consume_approval(self):
        owner = Actor(subject="alice")
        state = create_execution_plan("session", {
            "type": "plan",
            "tasks": [{"id": "send", "tool": "send_email", "parameters": {
                "recipient": "a@example.com", "subject": "Subject", "body": "Body"
            }}],
        }, actor=owner)
        approval = run_plan(state["execution_id"])
        result = approve_task(approval["approval_id"], actor=Actor(subject="mallory"))
        self.assertEqual(result["type"], "error")
        self.assertIsNotNone(workflow_store.get_approval(approval["approval_id"]))

    def test_normalized_result_and_partial_outcome(self):
        state = {
            "tasks": {
                "ok": {"status": "completed"},
                "bad": {"status": "failed"},
            }
        }
        self.assertEqual(determine_execution_outcome(state), "partially_completed")

        execution = create_execution_plan("session", {
            "type": "plan",
            "tasks": [{"id": "users", "tool": "list_users", "parameters": {}}],
        })
        with patch("opspilot.core.executor.dispatch", return_value={"success": True, "users": []}):
            result = run_plan(execution["execution_id"])
        self.assertEqual(result["type"], "completed")
        self.assertEqual(result["task_ledger"], [{
            "id": "users",
            "tool": "list_users",
            "depends_on": [],
            "status": "completed",
            "public_summary": "List users completed.",
            "error_code": None,
        }])
        tool_result = result["results"]["users"]
        self.assertEqual(tool_result["status"], "success")
        self.assertEqual(tool_result["data"]["users"], [])

    def test_tool_spec_adds_summary_when_a_handler_omits_one(self):
        result = get_tool_spec("list_users").normalize_result({"success": True, "users": []})
        self.assertEqual(result["public_summary"], "List users completed.")

    def test_graph_error_keeps_normalized_error_code(self):
        execution = create_execution_plan("session", {
            "type": "plan",
            "tasks": [{"id": "users", "tool": "list_users", "parameters": {}}],
        })
        with patch("opspilot.core.executor.dispatch", side_effect=GraphError("timeout", "timeout", retryable=True)):
            result = run_plan(execution["execution_id"])
        tool_result = result["results"]["users"]
        self.assertEqual(tool_result["error_code"], "timeout")
        self.assertTrue(tool_result["retryable"])

    def test_graph_error_exposes_safe_status_and_provider_code(self):
        execution = create_execution_plan("session", {
            "type": "plan",
            "tasks": [{"id": "users", "tool": "list_users", "parameters": {}}],
        })
        error = GraphError(
            "http_error",
            "The password does not meet the password policy requirements.",
            status_code=400,
            provider_code="Request_BadRequest",
        )
        with patch("opspilot.core.executor.dispatch", side_effect=error):
            result = run_plan(execution["execution_id"])
        tool_result = result["results"]["users"]
        self.assertEqual(tool_result["data"]["http_status"], 400)
        self.assertEqual(tool_result["data"]["provider_code"], "Request_BadRequest")
        self.assertIn("password policy", tool_result["public_summary"])

    def test_independent_read_only_tasks_run_concurrently(self):
        execution = create_execution_plan("session", {
            "type": "plan",
            "tasks": [
                {"id": "users", "tool": "list_users", "parameters": {}},
                {"id": "licenses", "tool": "list_available_licenses", "parameters": {}},
            ],
        })
        active = 0
        maximum_active = 0
        lock = threading.Lock()

        def slow_read(request):
            nonlocal active, maximum_active
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return {"success": True}

        with patch("opspilot.core.executor.dispatch", side_effect=slow_read):
            result = run_plan(execution["execution_id"])

        self.assertEqual(result["type"], "completed")
        self.assertGreaterEqual(maximum_active, 2)

    def test_draft_tasks_wait_for_approval_before_creating_mailbox_drafts(self):
        execution = create_execution_plan("session", {
            "type": "plan",
            "tasks": [
                {"id": "draft-one", "tool": "draft_email", "parameters": {
                    "recipient": "one@example.com", "subject": "One", "body": "One"
                }},
                {"id": "draft-two", "tool": "draft_email", "parameters": {
                    "recipient": "two@example.com", "subject": "Two", "body": "Two"
                }},
            ],
        })
        active = 0
        maximum_active = 0
        lock = threading.Lock()

        def slow_draft(request):
            nonlocal active, maximum_active
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            return {"success": True}

        with patch("opspilot.core.executor.dispatch", side_effect=slow_draft) as dispatch:
            result = run_plan(execution["execution_id"])

        self.assertEqual(result["type"], "approval_required")
        self.assertEqual(result["tool"], "draft_email")
        self.assertEqual(dispatch.call_count, 0)

    def test_expired_workflow_cancels_unstarted_tasks(self):
        execution = create_execution_plan("session", {
            "type": "plan",
            "tasks": [{"id": "users", "tool": "list_users", "parameters": {}}],
        })
        execution["deadline_at"] = "2000-01-01T00:00:00+00:00"
        workflow_store.save_execution(execution)

        result = run_plan(execution["execution_id"])

        self.assertEqual(result["type"], "cancelled")
        self.assertEqual(result["results"]["users"]["error_code"], "workflow_deadline_exceeded")

    def test_capacity_limit_returns_retryable_response(self):
        executor_module._execution_slots = threading.BoundedSemaphore(1)
        executor_module._execution_slots.acquire()
        try:
            result = run_plan("any-execution")
        finally:
            executor_module._execution_slots = threading.BoundedSemaphore(
                executor_module.WORKFLOW_MAX_ACTIVE_EXECUTIONS
            )

        self.assertEqual(result["error_code"], "workflow_capacity_exhausted")
        self.assertTrue(result["retryable"])

    def test_mixed_read_write_workflow_waits_for_and_resumes_after_approval(self):
        execution = create_execution_plan("session", {
            "type": "plan",
            "tasks": [
                {"id": "read", "tool": "list_users", "parameters": {}},
                {"id": "send", "tool": "send_email", "depends_on": ["read"], "parameters": {
                    "recipient": "a@example.com", "subject": "Update", "body": "Done"
                }},
            ],
        })
        with patch("opspilot.core.executor.dispatch", side_effect=[
            {"success": True, "message": "Found 1 user.", "users": []},
            {"success": True, "message": "Email sent to a@example.com."},
        ]) as dispatch:
            approval = run_plan(execution["execution_id"])
            self.assertEqual(approval["type"], "approval_required")
            result = approve_task(approval["approval_id"])

        self.assertEqual(result["type"], "completed")
        self.assertEqual([call.args[0]["tool"] for call in dispatch.call_args_list], [
            "list_users", "send_email"
        ])

    def test_failed_dependency_blocks_dependent_task(self):
        execution = create_execution_plan("session", {
            "type": "plan",
            "tasks": [
                {"id": "read", "tool": "list_users", "parameters": {}},
                {"id": "draft", "tool": "draft_email", "depends_on": ["read"], "parameters": {
                    "recipient": "a@example.com", "subject": "Update", "body": "Done"
                }},
            ],
        })
        with patch("opspilot.core.executor.dispatch", return_value={"success": False, "message": "Read failed."}) as dispatch:
            result = run_plan(execution["execution_id"])

        self.assertEqual(result["type"], "failed")
        self.assertEqual(result["task_ledger"][1]["status"], "blocked")
        self.assertEqual(dispatch.call_count, 1)

    def test_independent_failure_and_success_report_partial_completion(self):
        execution = create_execution_plan("session", {
            "type": "plan",
            "tasks": [
                {"id": "users", "tool": "list_users", "parameters": {}},
                {"id": "licenses", "tool": "list_available_licenses", "parameters": {}},
            ],
        })

        def mixed_result(request):
            if request["tool"] == "list_users":
                return {"success": False, "message": "Users unavailable."}
            return {"success": True, "message": "Found 2 available tenant licenses."}

        with patch("opspilot.core.executor.dispatch", side_effect=mixed_result):
            result = run_plan(execution["execution_id"])

        self.assertEqual(result["type"], "partially_completed")
        response = final_response(result)
        self.assertIn("Found 2 available tenant licenses.", response["message"])
        self.assertIn("partially completed", response["message"])

    def test_terminal_response_exposes_public_task_ledger_not_private_data(self):
        result = {
            "type": "completed",
            "execution_id": "execution-1",
            "session_id": "session-1",
            "task_ledger": [{
                "id": "licenses",
                "tool": "list_available_licenses",
                "status": "completed",
                "public_summary": "",
                "error_code": None,
            }],
            "results": {
                "licenses": {
                    "success": True,
                    "data": {
                        "licenses": [{"skuPartNumber": "FLOW_FREE"}],
                        "body": "raw provider payload",
                        "access_token": "secret-token",
                        "Token": "another-secret-token",
                    },
                    "private_data": {"temporary_password": "one-time-secret"},
                }
            },
        }

        response = final_response(result)
        task = response["execution"]["tasks"][0]

        self.assertEqual(response["type"], "final")
        self.assertEqual(response["session_id"], "session-1")
        self.assertEqual(response["execution"]["outcome"], "completed")
        self.assertEqual(task["tool"], "list_available_licenses")
        self.assertEqual(task["data"], {
            "licenses": [{"skuPartNumber": "FLOW_FREE"}]
        })
        self.assertNotIn("action(s)", response["message"])
        self.assertNotIn("body", task["data"])
        self.assertNotIn("access_token", task["data"])
        self.assertNotIn("Token", task["data"])
        self.assertNotIn("private_data", task)

    def test_execution_report_preserves_terminal_outcomes_and_task_states(self):
        for outcome, task_status, error_code in [
            ("completed", "completed", None),
            ("partially_completed", "failed", "graph_error"),
            ("failed", "failed", "graph_error"),
            ("cancelled", "cancelled", None),
        ]:
            report = build_execution_report({
                "type": outcome,
                "execution_id": "execution-1",
                "task_ledger": [{
                    "id": "task-1",
                    "tool": "list_users",
                    "status": task_status,
                    "public_summary": "Safe summary",
                    "error_code": error_code,
                }],
                "results": {"task-1": {"data": {"users": []}}},
            })
            self.assertEqual(report["outcome"], outcome)
            self.assertEqual(report["tasks"][0]["status"], task_status)
            self.assertEqual(report["tasks"][0]["error_code"], error_code)

    def test_final_response_includes_every_successful_task_summary(self):
        summaries = [
            "Cepha G has 1 license: Flow Free.",
            "harmit.kaur has 1 license: Flow Free.",
            "Info has 1 license: Flow Free.",
            "Jeevan has 1 license: Flow Free.",
            "John Doe has 1 license: Flow Free.",
            "Kamal has 1 license: Flow Free.",
        ]
        result = {
            "type": "completed",
            "execution_id": "execution-1",
            "results": {
                f"license-{index}": {
                    "success": True,
                    "data": {},
                    "public_summary": summary,
                }
                for index, summary in enumerate(summaries)
            },
        }

        response = final_response(result)

        for summary in summaries:
            self.assertIn(summary, response["message"])
        self.assertEqual(response["message"].count("has 1 license"), len(summaries))

    def test_email_summary_is_the_answer_not_the_retrieval_result(self):
        response = final_response({
            "type": "completed",
            "task_ledger": [
                {"id": "emails", "depends_on": [], "status": "completed"},
                {"id": "summary", "depends_on": ["emails"], "status": "completed"},
                {"id": "licenses", "depends_on": [], "status": "completed"},
            ],
            "results": {
                "emails": {
                    "success": True,
                    "public_summary": "Found 10 recent email(s).",
                    "data": {"emails": [{"subject": "Verification code 123456", "from": "service@example.com"}]},
                },
                "summary": {
                    "success": True,
                    "public_summary": "A summary was generated.",
                    "data": {"summary": "The latest email confirms your account and contains a verification code 123456."},
                },
                "licenses": {
                    "success": True,
                    "public_summary": "John Doe has 1 license: Flow Free.",
                    "data": {},
                },
            },
        })
        self.assertIn("confirms your account", response["message"])
        self.assertIn("John Doe has 1 license", response["message"])
        self.assertNotIn("Emails (1)", response["message"])
        self.assertNotIn("Found 10", response["message"])
        self.assertNotIn("123456", response["message"])

    def test_json_encoded_ai_summary_is_converted_to_safe_prose(self):
        response = final_response({
            "type": "completed",
            "task_ledger": [
                {"id": "summary", "depends_on": [], "status": "completed"},
            ],
            "results": {"summary": {
                "success": True,
                "public_summary": "An email was summarized.",
                "data": {"summary": json.dumps({
                    "sender": "The Trimble Team",
                    "subject": "Your Trimble Identity Verification Code",
                    "summary": "It contains a verification code 123456 that expires in 10 minutes.",
                })},
            }},
        })
        self.assertEqual(
            response["message"],
            "It contains a verification code [REDACTED] that expires in 10 minutes.",
        )
        self.assertNotIn("{", response["message"])
        self.assertNotIn("123456", response["message"])

    @patch("opspilot.tools.ai.ask", return_value="A plain email summary.")
    @patch("opspilot.tools.ai.get_email")
    def test_email_summary_requests_plain_text_with_safe_context(self, get_email_mock, ask_mock):
        get_email_mock.return_value = {
            "success": True,
            "email": {
                "from": "sender@example.com",
                "subject": "Status update",
                "body": "Body text",
            },
        }
        result = summarize_email({"email_id": "message-id"})
        prompt = ask_mock.call_args.args[0]

        self.assertEqual(result["summary"], "A plain email summary.")
        self.assertIn("plain conversational text, never JSON", prompt)
        self.assertIn("sender@example.com", prompt)
        self.assertIn("Status update", prompt)

    def test_email_list_is_readable_and_redacts_verification_codes(self):
        response = final_response({
            "type": "completed",
            "results": {"emails": {
                "success": True,
                "public_summary": "Found 1 unread email.",
                "data": {"emails": [{
                    "id": "graph-message-id",
                    "subject": "Your verification code is 654321",
                    "from": "service@example.com",
                }]},
            }},
        })
        self.assertIn("Emails (1)", response["message"])
        self.assertIn("[REDACTED]", response["message"])
        self.assertNotIn("654321", response["message"])
        self.assertNotIn("graph-message-id", response["message"])

    def test_empty_result_has_a_conversational_answer(self):
        response = final_response({
            "type": "completed",
            "results": {"emails": {
                "success": True,
                "public_summary": "Found 0 unread email(s).",
                "data": {"emails": []},
            }},
        })
        self.assertEqual(response["message"], "No emails found.")

    def test_failed_workflow_explains_the_failure(self):
        response = final_response({
            "type": "failed",
            "results": {"users": {
                "success": False,
                "public_summary": "Microsoft Graph could not complete the requested operation.",
                "data": {},
            }},
        })
        self.assertIn("could not be completed", response["message"])
        self.assertIn("Microsoft Graph", response["message"])

    def test_successful_write_uses_its_concise_confirmation(self):
        response = final_response({
            "type": "completed",
            "results": {"send": {
                "success": True,
                "public_summary": "Email sent to a@example.com.",
                "data": {},
            }},
        })
        self.assertEqual(response["message"], "Email sent to a@example.com.")

    def test_public_projection_removes_ids_and_sensitive_text(self):
        projected = build_execution_report({
            "type": "completed",
            "task_ledger": [{"id": "mail", "tool": "get_email", "status": "completed", "public_summary": "", "error_code": None}],
            "results": {"mail": {"data": {
                "id": "graph-id", "subject": "Verification code 123456", "body": "secret", "access_token": "token"
            }}},
        })
        data = projected["tasks"][0]["data"]
        self.assertNotIn("id", data)
        self.assertNotIn("body", data)
        self.assertNotIn("access_token", data)
        self.assertNotIn("123456", data["subject"])
        self.assertEqual(sanitize_public_text("OTP 123456"), "OTP [REDACTED]")
        self.assertEqual(
            sanitize_public_text("Password: hunter2"),
            "Password [REDACTED]",
        )

    def test_approval_state_has_no_terminal_execution_report(self):
        approval = {"type": "approval_required", "approval_id": "approval-1"}
        self.assertNotIn("execution", approval)

    def test_frontend_displays_only_the_human_readable_response(self):
        with open("opspilot/web/app.js", encoding="utf-8") as frontend:
            source = frontend.read()

        self.assertIn('addMessage(\n            "assistant",\n            response.message', source)
        self.assertNotIn("response.execution", source)
        self.assertNotIn("addExecutionReport", source)
        self.assertNotIn("execution-report", source)
        self.assertNotIn("JSON.stringify(task.data", source)
        self.assertNotIn("[UI] Agent response:", source)
        self.assertNotIn("[UI] Approval required:", source)
        self.assertIn('case "draft_email"', source)
        self.assertIn("Draft preview", source)

    @patch("opspilot.tools.licenses.graph_get")
    @patch("opspilot.tools.licenses.find_users")
    def test_license_tool_supplies_public_summary_for_generic_renderer(
        self, mock_find_users, mock_graph_get
    ):
        mock_find_users.return_value = [{"id": "user-1", "displayName": "John Doe"}]
        mock_graph_get.return_value = {
            "value": [{"skuPartNumber": "FLOW_FREE", "skuId": "sku-1"}]
        }

        result = get_tool_spec("list_user_licenses").normalize_result(
            list_user_licenses({"user": "John Doe"})
        )

        self.assertEqual(result["public_summary"], "John Doe has 1 license: Flow Free.")
        self.assertEqual(result["data"]["licenses"][0]["skuId"], "sku-1")

        response = final_response({
            "type": "completed",
            "execution_id": "execution-1",
            "task_ledger": [{
                "id": "licenses",
                "tool": "list_user_licenses",
                "status": "completed",
                "public_summary": result["public_summary"],
                "error_code": None,
            }],
            "results": {"licenses": result},
        })
        self.assertEqual(response["message"], "John Doe has 1 license: Flow Free.")


if __name__ == "__main__":
    unittest.main()
