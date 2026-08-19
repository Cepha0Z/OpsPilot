import uuid
import re

from actor import Actor
from audit_log import SENSITIVE_KEYS, log_event, redact
from models import Plan, sanitize_public_text
from workflow_store import workflow_store


PRIVATE_RESULT_FIELDS = SENSITIVE_KEYS | {
    "private_data", "raw_response", "raw_provider_data", "torecipients",
    "id", "user_id", "email_id", "draft_id", "sku_id", "skuid", "serviceplanid",
}


# Acknowledgements are chat turns, not requests to plan or execute Graph work.
# Keep this deliberately narrow so an actual request containing "thanks" still
# reaches the planner.
_ACKNOWLEDGEMENT_PATTERN = re.compile(
    r"^\s*(?:thanks|thank\s+you|thx)"
    r"(?:[\s,!.]+(?:next|got\s+it|ok(?:ay)?|cool|great|that'?s\s+all))?"
    r"[\s!.]*$",
    re.IGNORECASE,
)


def acknowledgement_response(user_message, session_id):
    if not _ACKNOWLEDGEMENT_PATTERN.fullmatch(user_message):
        return None
    return {
        "type": "final",
        "session_id": session_id,
        "message": "You’re welcome. What would you like to do next?",
    }


def unsupported_capability_response(user_message, session_id):
    """Reject workflow shapes that the registered tools cannot support."""
    text = user_message.lower()
    mail_history = any(phrase in text for phrase in (
        "haven't received an email from me", "have not received an email from me",
        "hasn't received an email from me", "has not received an email from me",
        "who did i email", "sent mail history", "email history",
    ))
    dynamic_fan_out = bool(re.search(r"\bfor (?:each|every) (?:user|person|employee|one)\b", text))
    dataset_comparison = bool(re.search(r"\b(?:users|people|employees) who\b", text)) and any(
        word in text for word in ("haven't", "have not", "without", "not received", "missing", "compared")
    )
    recent_users = "most recent users" in text or "newest users" in text

    if mail_history:
        message = "Nebulous cannot yet compare tenant users with Sent Items or mailbox history. It can list users and search messages separately, but it cannot determine who has not received an email."
    elif dynamic_fan_out:
        message = "Nebulous cannot yet apply a workflow dynamically to every user returned by another task. Please name the specific users, or request a supported fixed set of actions."
    elif dataset_comparison:
        message = "Nebulous cannot yet compare two Graph datasets to select matching users. Please request one supported lookup or provide the specific users to inspect."
    elif recent_users:
        message = "Nebulous cannot currently rank users by when their accounts were created. The available user list is not a recent-user report."
    else:
        return None
    return {
        "type": "error",
        "error_code": "unsupported_capability",
        "session_id": session_id,
        "message": message,
    }


def public_result_data(value):
    """Make executor data display-safe without exposing raw/provider secrets."""
    if isinstance(value, dict):
        return {
            key: public_result_data(item)
            for key, item in value.items()
            if key.lower() not in PRIVATE_RESULT_FIELDS
        }
    if isinstance(value, list):
        return [public_result_data(item) for item in value]
    if isinstance(value, str):
        return sanitize_public_text(value)
    return value


def build_execution_report(result):
    """Stable public execution contract used by the API and frontend."""
    results = result.get("results", {})
    task_ledger = result.get("task_ledger", [])
    tasks = []
    for task in task_ledger:
        task_result = results.get(task["id"], {})
        tasks.append({
            **task,
            "data": public_result_data(task_result.get("data", {})),
        })
    return {
        "outcome": result.get("type"),
        "execution_id": result.get("execution_id"),
        "tasks": tasks,
    }


def final_response(result, session_id=None):
    return {
        "type": "final",
        "session_id": session_id or result.get("session_id"),
        "execution_id": result.get("execution_id"),
        "message": build_final_message(result),
        "execution": build_execution_report(result),
    }

from planner import (
    plan_request,
    continue_plan,
    plan_diagnostic,
    parse_plan,
)

from executor import (
    create_execution_plan,
    run_plan,
    approve_task,
    reject_task,
)


# ============================================================
# SESSION STATE
# ============================================================

# ============================================================
# LOGGING
# ============================================================

def log(message):
    log_event("agent.log", message=message)


def log_json(label, value):
    log_event("agent.data", label=label, value=redact(value))


# ============================================================
# RUN AGENT
# ============================================================

def run_agent(
    user_message,
    session_id=None,
    actor=None,
    idempotency_key=None,
):

    actor = actor or Actor.local()

    if session_id is None:

        session_id = str(
            uuid.uuid4()
        )

    acknowledgement = acknowledgement_response(user_message, session_id)
    if acknowledgement:
        log_event("agent.acknowledgement", session_id=session_id)
        return acknowledgement

    unsupported = unsupported_capability_response(user_message, session_id)
    if unsupported:
        log_event("agent.unsupported_capability", session_id=session_id)
        return unsupported

    log("")
    log("=" * 70)
    log("[AGENT] REQUEST")
    log("=" * 70)

    log(
        f"[AGENT] Session: {session_id}"
    )

    log_event("agent.request", session_id=session_id, actor_subject=actor.subject)

    # ========================================================
    # EXISTING CLARIFICATION?
    # ========================================================

    stored_clarification = workflow_store.get_clarification(session_id)
    if stored_clarification and stored_clarification["actor_subject"] != actor.subject:
        return {"type": "error", "session_id": session_id, "message": "This session belongs to a different actor."}

    clarification = workflow_store.claim_clarification(session_id, actor.subject)

    if clarification:

        log("")
        log(
            "[AGENT] Answering pending clarification."
        )

        original_request = clarification["original_request"]
        partial_plan = clarification["partial_plan"]

        log(
            "[AGENT] Sending clarification "
            "answer to planner..."
        )

        try:

            raw_plan = continue_plan(
                original_request,
                partial_plan,
                user_message
            )

        except Exception:

            workflow_store.reopen_clarification(session_id)
            log_event("planner.continue_failed", session_id=session_id)

            return {
                "type": "error",
                "session_id": session_id,
                "message": (
                    "I couldn't continue the "
                    "execution plan."
                )
            }

    else:

        # ====================================================
        # FIRST AND NORMAL GEMINI CALL
        # ====================================================

        log("")
        log(
            "[AGENT] Creating complete plan..."
        )

        try:

            raw_plan = plan_request(
                user_message
            )

        except Exception as error:

            log_event(
                "planner.initial_failed",
                session_id=session_id,
                error_type=type(error).__name__,
            )

            return {
                "type": "error",
                "session_id": session_id,
                "message": (
                    "The planning service is temporarily unavailable. "
                    "Please retry your request."
                )
            }

    # ========================================================
    # LOG PLANNER RESPONSE
    # ========================================================

    log_event("planner.response_received", session_id=session_id)

    # ========================================================
    # PARSE
    # ========================================================

    try:

        plan = parse_plan(
            raw_plan
        )

    except Exception as error:

        error_code, message = plan_diagnostic(error)
        log_event("planner.plan_invalid", session_id=session_id, error_code=error_code)

        return {
            "type": "error",
            "error_code": error_code,
            "session_id": session_id,
            "message": message,
        }

    # ========================================================
    # CLARIFICATION
    # ========================================================

    if plan.get(
        "type"
    ) == "clarification":

        partial_plan = {
            "type": "plan",
            "tasks": plan.get(
                "tasks",
                []
            )
        }

        workflow_store.save_clarification(
            session_id,
            actor,
            clarification["original_request"] if clarification else user_message,
            partial_plan,
            plan["question"],
        )

        log("")
        log("=" * 70)
        log("[AGENT] WAITING FOR USER INPUT")
        log("=" * 70)

        log(
            f"[AGENT] Question: "
            f"{plan['question']}"
        )

        log_json(
            "[AGENT] Saved partial plan:",
            partial_plan
        )

        return {
            "type": "clarification",
            "session_id": session_id,
            "message": plan[
                "question"
            ]
        }

    # ========================================================
    # COMPLETE PLAN
    # ========================================================

    typed_plan = Plan.from_dict(plan)

    log("")
    log("=" * 70)
    log("[PLANNER] COMPLETE PLAN")
    log("=" * 70)

    log_event("planner.plan_validated", session_id=session_id, task_count=len(typed_plan.tasks))

    # ========================================================
    # CREATE EXECUTION
    # ========================================================

    try:

        state = create_execution_plan(
            session_id,
            typed_plan,
            actor=actor,
            idempotency_key=idempotency_key,
        )

    except Exception:

        log_event("executor.plan_creation_failed", session_id=session_id)

        return {
            "type": "error",
            "session_id": session_id,
            "message": (
                "I couldn't create the "
                "execution workflow."
            )
        }

    execution_id = state[
        "execution_id"
    ]

    # ========================================================
    # EXECUTE READY TASKS
    # ========================================================

    result = run_plan(
        execution_id
    )


    if not isinstance(
        result,
        dict
    ):

        return {
            "type": "error",
            "session_id": session_id,
            "message": (
                "The executor returned "
                "an invalid response."
            )
        }

    result[
        "session_id"
    ] = session_id

    # ========================================================
    # COMPLETE
    # ========================================================

    if result.get("type") in {"completed", "partially_completed", "cancelled", "failed"}:

        return final_response(result, session_id=session_id)

    return result


# ============================================================
# APPROVE
# ============================================================

def approve_action(
    approval_id,
    actor=None,
):

    log("")
    log("=" * 70)
    log("[AGENT] APPROVING TASK")
    log("=" * 70)

    result = approve_task(
        approval_id,
        actor=actor or Actor.local(),
    )

    if not isinstance(
        result,
        dict
    ):

        return {
            "type": "error",
            "message":
                "Invalid executor response."
        }

    if result.get("type") in {"completed", "partially_completed", "cancelled", "failed"}:

        return final_response(result)

    return result


# ============================================================
# REJECT
# ============================================================

def reject_action(
    approval_id,
    actor=None,
):

    log("")
    log("=" * 70)
    log("[AGENT] REJECTING TASK")
    log("=" * 70)

    result = reject_task(
        approval_id,
        actor=actor or Actor.local(),
    )

    if not isinstance(
        result,
        dict
    ):

        return {
            "type": "error",
            "message":
                "Invalid executor response."
        }

    if result.get("type") in {"completed", "partially_completed", "cancelled", "failed"}:

        return final_response(result)

    return result


# ============================================================
# FINAL MESSAGE
# ============================================================


def _list_email_answer(emails):
    if not emails:
        return "No emails found."
    lines = [f"Emails ({len(emails)}):"]
    for email in emails:
        if not isinstance(email, dict):
            continue
        subject = sanitize_public_text(email.get("subject", "(No subject)"))
        sender = sanitize_public_text(email.get("from", "Unknown sender"))
        lines.append(f"- {subject} — {sender}")
    return "\n".join(lines)


def _list_user_answer(users):
    if not users:
        return "No users found."
    lines = [f"Users ({len(users)}):"]
    for user in users:
        if not isinstance(user, dict):
            continue
        name = sanitize_public_text(user.get("displayName", "Unknown"))
        email = sanitize_public_text(user.get("userPrincipalName", ""))
        department = sanitize_public_text(user.get("department", ""))
        suffix = f" ({department})" if department else ""
        lines.append(f"- {name}{suffix}" + (f" — {email}" if email else ""))
    return "\n".join(lines)


def _license_answer(data, fallback):
    licenses = data.get("licenses")
    if not isinstance(licenses, list):
        return fallback
    if data.get("user"):
        return fallback
    if not licenses:
        return "No tenant licenses found."
    names = [
        sanitize_public_text(str(item.get("skuPartNumber", "Unknown license")).replace("_", " ").title())
        for item in licenses if isinstance(item, dict)
    ]
    return f"Available tenant licenses ({len(names)}): {', '.join(names)}."


def _plain_ai_text(value):
    """Return prose from a model response, never its serialized structure."""
    if isinstance(value, dict):
        for field in ("summary", "answer", "message", "content", "text", "reply"):
            if field in value:
                return _plain_ai_text(value[field])
        return ""
    if not isinstance(value, str):
        return ""

    text = value.strip()
    if text[:1] in {"{", "["}:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            pass
        else:
            return _plain_ai_text(parsed)
    return sanitize_public_text(text)


def _successful_answer(value):
    data = value.get("data", {})
    if not isinstance(data, dict):
        return sanitize_public_text(value.get("public_summary", ""))

    # AI-generated summaries and replies answer the request directly and take
    # precedence over retrieval steps that supplied their input.
    for field in ("summary", "reply"):
        text = _plain_ai_text(data.get(field))
        if text:
            return text
    if isinstance(data.get("emails"), list):
        return _list_email_answer(data["emails"])
    if isinstance(data.get("users"), list):
        return _list_user_answer(data["users"])
    if isinstance(data.get("licenses"), list):
        return _license_answer(data, sanitize_public_text(value.get("public_summary", "")))
    profile = data.get("data")
    if isinstance(profile, dict) and profile.get("displayName"):
        name = sanitize_public_text(profile["displayName"])
        department = sanitize_public_text(profile.get("department", ""))
        status = "active" if profile.get("accountEnabled") else "disabled"
        return f"{name} is {status}" + (f" in {department}." if department else ".")
    return sanitize_public_text(value.get("public_summary", ""))


def build_final_message(result):
    """Compose a conversational answer from safe normalized task results."""
    results = result.get("results", {})
    successes = []
    failures = []
    direct_task_ids = set()

    for task_id, value in results.items():
        if not isinstance(value, dict):
            continue
        if value.get("success") is False:
            failure = sanitize_public_text(value.get("public_summary", ""))
            if failure:
                failures.append(failure)
            continue
        answer = _successful_answer(value)
        if answer:
            successes.append((task_id, answer))
        data = value.get("data", {})
        if isinstance(data, dict) and any(_plain_ai_text(data.get(field)) for field in ("summary", "reply")):
            direct_task_ids.add(task_id)

    dependencies = {
        task.get("id"): set(task.get("depends_on", []))
        for task in result.get("task_ledger", [])
        if isinstance(task, dict) and task.get("id")
    }

    def supports_direct_answer(task_id):
        pending = list(direct_task_ids)
        visited = set()
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            for dependency in dependencies.get(current, set()):
                if dependency == task_id:
                    return True
                pending.append(dependency)
        return False

    outcome = result.get("type", "completed")
    # A summary/reply replaces only the retrieval tasks that directly fed it.
    # Independent task results remain visible in the same final answer.
    displayed_successes = [
        answer for task_id, answer in successes
        if not (direct_task_ids and supports_direct_answer(task_id))
    ]
    body = "\n\n".join(displayed_successes)

    if outcome == "cancelled":
        return "The requested workflow was cancelled." + (f"\n\n{body}" if body else "")
    if outcome == "partially_completed":
        failure_text = "\n".join(f"- {item}" for item in failures) or "- One or more tasks could not be completed."
        return (body + "\n\n" if body else "") + "The workflow partially completed.\nFailed:\n" + failure_text
    if outcome == "failed":
        return "The workflow could not be completed.\n" + ("\n".join(f"- {item}" for item in failures) if failures else "- The requested operation failed.")
    if body:
        return body
    return "The workflow completed successfully."
import json
