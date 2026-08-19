import copy
import json
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from .actor import Actor
from .audit_log import log_event, redact
from ..config.defaults import (
    WORKFLOW_DEADLINE_SECONDS,
    WORKFLOW_MAX_ACTIVE_EXECUTIONS,
    WORKFLOW_MAX_PARALLEL_READS,
)
from ..integrations.graph.client import GraphError
from .models import Plan, sanitize_public_text
from ..router.dispatcher import dispatch
from .tool_spec import TOOL_SPECS, get_tool_spec
from .workflow_store import APPROVAL_TTL_SECONDS, expiry, now, workflow_store


_execution_slots = threading.BoundedSemaphore(WORKFLOW_MAX_ACTIVE_EXECUTIONS)
_active_execution_lock = threading.Lock()
_active_execution_count = 0


# ============================================================
# TOOLS REQUIRING APPROVAL
# ============================================================

APPROVAL_REQUIRED = frozenset(
    name for name, spec in TOOL_SPECS.items() if spec.requires_approval
)


# ============================================================
# EXECUTION STATE
# ============================================================

# ============================================================
# TERMINAL LOGGING
# ============================================================

def timestamp():
    """
    Return the current terminal timestamp with milliseconds.
    """

    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def log(message):
    """
    Print immediately to the terminal.
    """

    log_event("executor.log", message=message)


def log_json(label, value):
    """
    Pretty-print JSON immediately.
    """

    log_event("executor.data", label=label, value=redact(value))


def log_separator():
    print(
        f"[{timestamp()}] " + "-" * 70,
        flush=True
    )


# ============================================================
# SAFE JSON
# ============================================================

def safe_json(value):

    return json.dumps(
        value,
        ensure_ascii=False,
        default=str
    )


# ============================================================
# CREATE EXECUTION PLAN
# ============================================================

def create_execution_plan(
    session_id,
    plan,
    actor=None,
    idempotency_key=None,
):
    """
    Convert the planner's JSON plan into executable state.

    Gemini is NOT called here.
    """

    if isinstance(plan, Plan):
        plan = plan.to_dict()

    actor = actor or Actor.local()
    execution_id = str(uuid.uuid4())

    state = {
        "execution_id": execution_id,
        "session_id": session_id,
        "actor": actor,
        "tasks": {},
        "results": {},
        "status": "running",
        "created_at": timestamp(),
        "deadline_at": expiry(WORKFLOW_DEADLINE_SECONDS),
    }

    for task in plan.get("tasks", []):

        task_copy = copy.deepcopy(task)

        task_copy.setdefault(
            "depends_on",
            []
        )

        task_copy.setdefault(
            "parameters",
            {}
        )

        task_copy.setdefault(
            "requires_approval",
            get_tool_spec(task_copy["tool"]).requires_approval
        )

        task_copy["status"] = "pending"

        state["tasks"][
            task_copy["id"]
        ] = task_copy

    state = workflow_store.create_execution(
        state,
        idempotency_key=idempotency_key,
    )

    log("")
    log("=" * 70)
    log("[EXECUTOR] PLAN CREATED")
    log("=" * 70)

    log(
        f"[EXECUTOR] Execution ID: {execution_id}"
    )

    log(
        f"[EXECUTOR] Tasks: {len(state['tasks'])}"
    )

    for task in state["tasks"].values():

        log(
            f"[EXECUTOR] "
            f"{task['id']} -> "
            f"{task['tool']} | "
            f"depends_on={task.get('depends_on', [])} | "
            f"approval={task.get('requires_approval', False)}"
        )

    return state


# ============================================================
# REFERENCE RESOLUTION
# ============================================================

REFERENCE_PATTERN = re.compile(
    r"\{\{([^{}]+)\}\}"
)


def get_reference_value(
    reference,
    results
):
    """
    Resolve references such as:

        create_user.user
        create_user.user.id
        create_user.user.email

    Also supports array indexing:

        list_emails.emails[0].id
        list_users.users[0].id
    """

    import re

    reference = reference.strip()

    # --------------------------------------------------------
    # Split:
    #
    # list_emails.emails[0].id
    #
    # into:
    #
    # list_emails
    # emails
    # 0
    # id
    # --------------------------------------------------------

    parts = re.findall(
        r"[^.\[\]]+|\[\d+\]",
        reference
    )

    if not parts:
        raise ValueError(
            f"Invalid reference: {reference}"
        )

    # --------------------------------------------------------
    # First part is always the task ID
    # --------------------------------------------------------

    task_id = parts[0]

    if task_id not in results:

        raise ValueError(
            f"Referenced task '{task_id}' "
            f"has no result yet."
        )

    value = results[task_id]

    # --------------------------------------------------------
    # Walk through the reference
    # --------------------------------------------------------

    for part in parts[1:]:

        # ----------------------------------------------------
        # Array index
        # ----------------------------------------------------

        if part.startswith("["):

            if not isinstance(value, list):

                raise ValueError(
                    f"Cannot index non-list value "
                    f"with '{part}' in reference "
                    f"'{reference}'."
                )

            try:

                index = int(
                    part[1:-1]
                )

            except ValueError:

                raise ValueError(
                    f"Invalid array index '{part}' "
                    f"in reference '{reference}'."
                )

            if index >= len(value):

                raise ValueError(
                    f"Array index {index} is out of range "
                    f"in reference '{reference}'."
                )

            value = value[index]

            continue

        # ----------------------------------------------------
        # Dictionary field
        # ----------------------------------------------------

        if isinstance(value, dict):

            if part in value:
                value = value[part]
                continue

            # Normalized results keep legacy output fields under data. This
            # preserves existing planner references such as create.user_id.
            if isinstance(value.get("data"), dict) and part in value["data"]:
                value = value["data"][part]
                continue

            if isinstance(value.get("private_data"), dict) and part in value["private_data"]:
                value = value["private_data"][part]
                continue

            raise ValueError(
                f"Reference '{reference}' "
                f"could not find field "
                f"'{part}'."
            )

        # ----------------------------------------------------
        # Invalid traversal
        # ----------------------------------------------------

        raise ValueError(
            f"Cannot access '{part}' "
            f"in reference '{reference}'."
        )

    return value




def resolve_value(
    value,
    results
):
    """
    Recursively resolve planner references.

    Supports:

        {
            "$ref": "create_user.company_email"
        }

    and:

        "Username: {{create_user.company_email}}"
    """

    # --------------------------------------------------------
    # Dictionary
    # --------------------------------------------------------

    if isinstance(value, dict):

        if "$ref" in value:

            return get_reference_value(
                value["$ref"],
                results
            )

        return {
            key: resolve_value(
                item,
                results
            )
            for key, item in value.items()
        }

    # --------------------------------------------------------
    # List
    # --------------------------------------------------------

    if isinstance(value, list):

        return [
            resolve_value(
                item,
                results
            )
            for item in value
        ]

    # --------------------------------------------------------
    # String
    # --------------------------------------------------------

    if isinstance(value, str):

        matches = REFERENCE_PATTERN.findall(
            value
        )

        if not matches:
            return value

        # Entire string is one reference.
        if (
            len(matches) == 1
            and value.strip()
            == "{{" + matches[0] + "}}"
        ):

            return get_reference_value(
                matches[0],
                results
            )

        # Embedded references.
        for reference in matches:

            replacement = get_reference_value(
                reference,
                results
            )

            if isinstance(
                replacement,
                (dict, list)
            ):

                replacement = safe_json(
                    replacement
                )

            value = value.replace(
                "{{" + reference + "}}",
                str(replacement)
            )

        return value

    return value


# ============================================================
# DEPENDENCIES
# ============================================================

def dependencies_complete(
    task,
    state
):
    """
    True only when every dependency completed.
    """

    for dependency in task.get(
        "depends_on",
        []
    ):

        dependency_task = state[
            "tasks"
        ].get(
            dependency
        )

        if not dependency_task:
            return False

        if dependency_task["status"] != "completed":
            return False

    return True


def dependency_failed(
    task,
    state
):
    """
    True if any dependency failed,
    blocked, or cancelled.
    """

    for dependency in task.get(
        "depends_on",
        []
    ):

        dependency_task = state[
            "tasks"
        ].get(
            dependency
        )

        if not dependency_task:
            continue

        if dependency_task["status"] in {
            "failed",
            "blocked",
            "cancelled",
        }:

            return True

    return False


# ============================================================
# EXECUTE TOOL
# ============================================================

def execute_tool(
    task,
    state
):
    """
    Execute exactly one tool.

    IMPORTANT:
    No Gemini call happens here.
    """

    task_id = task["id"]
    tool = task["tool"]

    log("")
    log("=" * 70)
    log(f"[TOOL] START: {task_id}")
    log(f"[TOOL] Tool: {tool}")
    log("=" * 70)

    # --------------------------------------------------------
    # Resolve parameters
    # --------------------------------------------------------

    try:

        parameters = resolve_value(
            task.get(
                "parameters",
                {}
            ),
            state["results"]
        )

    except Exception as e:

        log_event("tool.parameter_resolution_failed", task_id=task_id, tool=tool)

        return {
            "success": False,
            "status": "failed",
            "data": {},
            "public_summary": "The task could not resolve a required previous result.",
            "error_code": "parameter_resolution_failed",
            "retryable": False,
        }

    log_event("tool.start", task_id=task_id, tool=tool)

    # --------------------------------------------------------
    # Build dispatcher request
    # --------------------------------------------------------

    request = {
        "tool": tool,
        "parameters": parameters
    }

    # --------------------------------------------------------
    # Execute
    # --------------------------------------------------------

    started = datetime.now()

    try:

        log(
            "[TOOL] Calling dispatcher..."
        )

        legacy_result = dispatch(
            request
        )
        result = get_tool_spec(tool).normalize_result(legacy_result)

    except GraphError as error:

        log_event(
            "tool.graph_error",
            task_id=task_id,
            tool=tool,
            error_code=error.code,
            http_status=error.status_code,
            provider_code=error.provider_code,
        )

        result = {
            "success": False,
            "status": "failed",
            "data": {
                "http_status": error.status_code,
                "provider_code": error.provider_code,
            },
            "public_summary": str(error),
            "error_code": error.code,
            "retryable": error.retryable,
        }

    except Exception:

        log_event("tool.exception", task_id=task_id, tool=tool)

        result = {
            "success": False,
            "status": "failed",
            "data": {},
            "public_summary": "The requested tool could not be completed.",
            "error_code": "tool_exception",
            "retryable": False,
        }

    elapsed = (
        datetime.now() - started
    ).total_seconds()

    # --------------------------------------------------------
    # Log result
    # --------------------------------------------------------

    if (
        isinstance(result, dict)
        and result.get("success") is False
    ):

        log(
            f"[TOOL] FAILED: "
            f"{tool} "
            f"after {elapsed:.3f}s"
        )

    else:

        log(
            f"[TOOL] COMPLETED: "
            f"{tool} "
            f"after {elapsed:.3f}s"
        )

    log_event(
        "tool.finish",
        task_id=task_id,
        tool=tool,
        success=result.get("success"),
        elapsed_seconds=elapsed,
        error_code=result.get("error_code"),
    )

    return result


# ============================================================
# CREATE APPROVAL
# ============================================================

def create_approval(
    state,
    task
):
    """
    Pause execution until the user approves this task.

    Parameters are resolved BEFORE the approval is created.

    The exact parameters are stored for execution; the API receives a
    redacted preview.
    """

    # --------------------------------------------------------
    # Resolve parameters first
    # --------------------------------------------------------

    try:

        parameters = resolve_value(
            task.get(
                "parameters",
                {}
            ),
            state["results"]
        )

    except Exception:

        log_event("approval.parameter_resolution_failed", task_id=task["id"], tool=task["tool"])

        task["status"] = "failed"

        state["results"][
            task["id"]
        ] = {
            "success": False,
            "status": "failed",
            "data": {},
            "public_summary": "The task could not resolve its approval parameters.",
            "error_code": "approval_parameter_resolution_failed",
            "retryable": False,
        }
        workflow_store.save_execution(state)

        return {
            "type": "error",
            "session_id": state["session_id"],
            "execution_id": state["execution_id"],
            "message": (
                "Could not prepare the approval request."
            )
        }

    # --------------------------------------------------------
    # Create approval ID
    # --------------------------------------------------------

    approval_id = str(
        uuid.uuid4()
    )

    # --------------------------------------------------------
    # Store approval
    # --------------------------------------------------------

    approval_expiry = expiry(APPROVAL_TTL_SECONDS)
    workflow_deadline = state.get("deadline_at")
    if workflow_deadline and workflow_deadline < approval_expiry:
        approval_expiry = workflow_deadline

    approval = {
        "approval_id": approval_id,
        "execution_id": state["execution_id"],
        "session_id": state["session_id"],
        "task_id": task["id"],
        "parameters": parameters,
        "actor_subject": state["actor"].subject,
        "expires_at": approval_expiry,
    }

    preview = None
    if task["tool"] == "draft_email":
        # The UI needs the proposed draft body to support a meaningful review,
        # but never receives the execution parameters or unredacted secrets.
        preview = {
            "recipient": sanitize_public_text(parameters.get("recipient", "")),
            "subject": sanitize_public_text(parameters.get("subject", "")),
            "body": sanitize_public_text(parameters.get("body", "")),
        }

    task["status"] = "waiting_approval"
    workflow_store.save_execution_with_approval(state, approval)

    # --------------------------------------------------------
    # Log
    # --------------------------------------------------------

    log("")
    log("=" * 70)
    log("[APPROVAL] APPROVAL REQUIRED")
    log("=" * 70)

    log(
        f"[APPROVAL] Task: {task['id']}"
    )

    log(
        f"[APPROVAL] Tool: {task['tool']}"
    )

    log(
        f"[APPROVAL] Approval ID: {approval_id}"
    )

    # --------------------------------------------------------
    # Return to API/UI
    # --------------------------------------------------------

    response = {
        "type": "approval_required",
        "approval_id": approval_id,
        "session_id": state["session_id"],
        "execution_id": state["execution_id"],
        "task_id": task["id"],
        "tool": task["tool"],
        "parameters": redact(parameters),
        "approval_summary": get_tool_spec(task["tool"]).approval_summary(parameters),
        "message": (
            "This action requires approval."
        )
    }
    if preview:
        response["preview"] = preview
    return response


# ============================================================
# RUN PLAN
# ============================================================

def determine_execution_outcome(state):
    """Compute a truthful terminal outcome from the complete task ledger."""
    statuses = [task["status"] for task in state["tasks"].values()]

    if statuses and all(status == "completed" for status in statuses):
        return "completed"

    successful = any(status == "completed" for status in statuses)
    failures = any(status == "failed" for status in statuses)
    cancellations = any(status in {"cancelled", "blocked"} for status in statuses)

    if successful and (failures or cancellations):
        return "partially_completed"
    if failures:
        return "failed"
    if cancellations:
        return "cancelled"
    return "failed"

def build_task_ledger(state):
    """Public task metadata; never expose inputs or private provider data."""
    ledger = []
    for task in state["tasks"].values():
        result = state["results"].get(task["id"], {})
        ledger.append({
            "id": task["id"],
            "tool": task["tool"],
            "depends_on": task.get("depends_on", []),
            "status": task["status"],
            "public_summary": result.get("public_summary", ""),
            "error_code": result.get("error_code"),
        })
    return ledger


def deadline_reached(state):
    deadline_at = state.get("deadline_at")
    return bool(deadline_at and now() >= deadline_at)


def cancel_expired_tasks(state):
    """Finish unstarted work truthfully when its workflow deadline passes."""
    for task in state["tasks"].values():
        if task["status"] in {"pending", "waiting_approval"}:
            task["status"] = "cancelled"
            state["results"][task["id"]] = {
                "success": False,
                "status": "failed",
                "data": {},
                "public_summary": "The workflow deadline expired before this task started.",
                "error_code": "workflow_deadline_exceeded",
                "retryable": False,
            }


def _record_execution_start(execution_id):
    global _active_execution_count
    with _active_execution_lock:
        _active_execution_count += 1
        active = _active_execution_count
    log_event("workflow.start", execution_id=execution_id, active_executions=active)


def _record_execution_finish(execution_id, started):
    global _active_execution_count
    with _active_execution_lock:
        _active_execution_count -= 1
        active = _active_execution_count
    log_event(
        "workflow.finish",
        execution_id=execution_id,
        active_executions=active,
        elapsed_seconds=(datetime.now() - started).total_seconds(),
    )


def _finish_terminal_plan(state):
    outcome = determine_execution_outcome(state)
    state["status"] = outcome
    workflow_store.save_execution(state)

    log("")
    log("=" * 70)
    log(f"[EXECUTOR] PLAN TERMINAL: {outcome}")
    log("=" * 70)
    for task in state["tasks"].values():
        log(f"[EXECUTOR] {task['id']}: {task['status']}")

    return {
        "type": outcome,
        "execution_id": state["execution_id"],
        "session_id": state["session_id"],
        "results": state["results"],
        "task_ledger": build_task_ledger(state),
    }


def _execute_ready_reads(state, tasks):
    """Run only dependency-free read tools concurrently; state updates stay serial."""
    for task in tasks:
        task["status"] = "running"
    workflow_store.save_execution(state)

    results = {}
    workers = max(1, min(WORKFLOW_MAX_PARALLEL_READS, len(tasks)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="opspilot-read") as pool:
        futures = {pool.submit(execute_tool, task, state): task for task in tasks}
        for future in as_completed(futures):
            task = futures[future]
            results[task["id"]] = future.result()

    for task in tasks:
        result = results[task["id"]]
        task["status"] = "failed" if result.get("success") is False else "completed"
        state["results"][task["id"]] = result
        workflow_store.save_execution(state)
        log(f"[EXECUTOR] Task {task['id']}: {task['status']}")


def run_plan(execution_id):
    """Apply the in-process capacity limit around one synchronous workflow run."""
    if not _execution_slots.acquire(blocking=False):
        log_event("workflow.capacity_exhausted", execution_id=execution_id)
        return {
            "type": "error",
            "execution_id": execution_id,
            "message": "OpsPilot is busy. Please retry this workflow shortly.",
            "error_code": "workflow_capacity_exhausted",
            "retryable": True,
        }
    started = datetime.now()
    _record_execution_start(execution_id)
    try:
        return _run_plan(execution_id)
    finally:
        _record_execution_finish(execution_id, started)
        _execution_slots.release()


def _run_plan(
    execution_id
):
    """
    Execute the complete plan.

    IMPORTANT:

    Gemini is NEVER called here.

    The planner already produced the complete plan.
    The executor simply follows it.
    """

    state = workflow_store.get_execution(execution_id)

    if not state:

        return {
            "type": "error",
            "message": (
                "Execution plan not found."
            )
        }

    log("")
    log("=" * 70)
    log("[EXECUTOR] RUNNING PLAN")
    log("=" * 70)

    while True:

        progress = False
        ready_reads = []

        if deadline_reached(state):
            cancel_expired_tasks(state)
            return _finish_terminal_plan(state)

        # ----------------------------------------------------
        # Check every task
        # ----------------------------------------------------

        for task_id, task in state[
            "tasks"
        ].items():

            # Already handled.
            if task["status"] in {
                "completed",
                "failed",
                "blocked",
                "cancelled",
                "waiting_approval",
                "running",
            }:

                continue

            # ------------------------------------------------
            # Dependency failure
            # ------------------------------------------------

            if dependency_failed(
                task,
                state
            ):

                task["status"] = "blocked"
                workflow_store.save_execution(state)

                log(
                    f"[EXECUTOR] BLOCKED: {task_id}"
                )

                log(
                    "[EXECUTOR] Dependency "
                    "failed/cancelled."
                )

                progress = True

                continue

            # ------------------------------------------------
            # Wait for dependencies
            # ------------------------------------------------

            if not dependencies_complete(
                task,
                state
            ):

                continue

            # ------------------------------------------------
            # Approval
            # ------------------------------------------------

            requires_approval = (
                task.get(
                    "requires_approval",
                    False
                )
                or get_tool_spec(task["tool"]).requires_approval
            )

            if requires_approval:

                # Preserve plan order: complete an earlier batch of reads
                # before pausing at a later approval-gated operation.
                if ready_reads:
                    break

                return create_approval(
                    state,
                    task
                )

            if get_tool_spec(task["tool"]).side_effect == "read":
                ready_reads.append(task)
                continue

            # Drafts remain sequential.  Finish earlier reads first so a
            # plan's ordered operations keep their existing semantics.
            if ready_reads:
                break

            # ------------------------------------------------
            # Execute
            # ------------------------------------------------

            task["status"] = "running"
            workflow_store.save_execution(state)

            result = execute_tool(
                task,
                state
            )

            if (
                isinstance(result, dict)
                and result.get("success") is False
            ):

                task["status"] = "failed"

            else:

                task["status"] = "completed"

            state[
                "results"
            ][task_id] = result
            workflow_store.save_execution(state)

            progress = True

            log(
                f"[EXECUTOR] Task "
                f"{task_id}: "
                f"{task['status']}"
            )

        if ready_reads:
            _execute_ready_reads(state, ready_reads)
            progress = True
            continue

        # ----------------------------------------------------
        # Find unfinished tasks
        # ----------------------------------------------------

        unfinished = [
            task
            for task in state[
                "tasks"
            ].values()
            if task["status"] not in {
                "completed",
                "failed",
                "blocked",
                "cancelled",
            }
        ]

        # ----------------------------------------------------
        # Waiting for approval
        # ----------------------------------------------------

        waiting = [
            task
            for task in state[
                "tasks"
            ].values()
            if task["status"]
            == "waiting_approval"
        ]

        if waiting:

            log(
                "[EXECUTOR] Waiting for "
                "user approval."
            )

            approval = workflow_store.get_pending_approval(execution_id)
            response = {
                "type": "approval_required",
                "execution_id": execution_id,
                "session_id": state["session_id"],
                "message": (
                    "Approval required."
                )
            }
            if approval:
                task = state["tasks"][approval["task_id"]]
                response.update({
                    "approval_id": approval["approval_id"],
                    "task_id": approval["task_id"],
                    "tool": task["tool"],
                    "parameters": redact(approval["parameters"]),
                    "approval_summary": get_tool_spec(task["tool"]).approval_summary(approval["parameters"]),
                })
            return response

        # ----------------------------------------------------
        # Complete
        # ----------------------------------------------------

        if not unfinished:
            return _finish_terminal_plan(state)

        # ----------------------------------------------------
        # No progress
        # ----------------------------------------------------

        if not progress:

            state["status"] = "failed"
            workflow_store.save_execution(state)

            log(
                "[EXECUTOR] ERROR: "
                "Plan cannot make further progress."
            )

            for task in state[
                "tasks"
            ].values():

                log(
                    f"[EXECUTOR] "
                    f"{task['id']}: "
                    f"{task['status']}"
                )

            return {
                "type": "error",
                "execution_id": execution_id,
                "session_id": state["session_id"],
                "message": (
                    "The execution plan could "
                    "not make further progress."
                )
            }


# ============================================================
# APPROVE TASK
# ============================================================

def approve_task(
    approval_id,
    actor=None,
):
    """
    Approve ONE task and resume the SAME plan.

    NO Gemini call.
    """

    approval = workflow_store.get_approval(approval_id)

    if not approval:

        log(
            "[APPROVAL] Approval not found "
            "or expired."
        )

        return {
            "type": "error",
            "message": (
                "Approval not found or expired."
            )
        }

    execution_id = approval[
        "execution_id"
    ]

    state = workflow_store.get_execution(execution_id)

    if not state:

        return {
            "type": "error",
            "message": (
                "Execution plan no longer exists."
            )
        }

    actor = actor or Actor.local()
    state_actor = state.get("actor", Actor.local())
    if actor.subject != state_actor.subject:
        return {
            "type": "error",
            "session_id": state["session_id"],
            "execution_id": execution_id,
            "message": "This approval belongs to a different actor.",
        }

    approval = workflow_store.claim_approval(approval_id, actor.subject, "consumed")
    if not approval:
        return {"type": "error", "message": "Approval not found or expired."}

    task_id = approval[
        "task_id"
    ]

    task = state[
        "tasks"
    ].get(
        task_id
    )

    if not task:

        return {
            "type": "error",
            "message": (
                "Task no longer exists."
            )
        }

    # --------------------------------------------------------
    # Make sure task is actually waiting for approval
    # --------------------------------------------------------

    if task["status"] != "waiting_approval":

        return {
            "type": "error",
            "session_id": state["session_id"],
            "execution_id": execution_id,
            "message": (
                "This task is no longer waiting for approval."
            )
        }

    log("")
    log("=" * 70)
    log("[APPROVAL] APPROVED")
    log("=" * 70)

    log(
        f"[APPROVAL] Task: {task_id}"
    )

    log(
        f"[APPROVAL] Tool: {task['tool']}"
    )

    # --------------------------------------------------------
    # Execute approved task
    # --------------------------------------------------------

    task["status"] = "running"
    workflow_store.save_execution(state)

    result = execute_tool(
        task,
        state
    )

    if (
        isinstance(result, dict)
        and result.get("success") is False
    ):

        task["status"] = "failed"

    else:

        task["status"] = "completed"

    state[
        "results"
    ][task_id] = result
    workflow_store.save_execution(state)

    # --------------------------------------------------------
    # Continue SAME plan
    #
    # Absolutely NO Gemini call.
    # --------------------------------------------------------

    log(
        "[EXECUTOR] Resuming existing "
        "execution plan."
    )

    return run_plan(
        execution_id
    )


# ============================================================
# REJECT TASK
# ============================================================

def reject_task(
    approval_id,
    actor=None,
):
    """
    Reject one task.

    The task becomes cancelled.

    Tasks depending on it become blocked.

    Independent tasks can still execute.
    """

    approval = workflow_store.get_approval(approval_id)

    if not approval:

        return {
            "type": "error",
            "message": (
                "Approval not found or expired."
            )
        }

    execution_id = approval[
        "execution_id"
    ]

    state = workflow_store.get_execution(execution_id)

    if not state:

        return {
            "type": "error",
            "message": (
                "Execution plan no longer exists."
            )
        }

    actor = actor or Actor.local()
    state_actor = state.get("actor", Actor.local())
    if actor.subject != state_actor.subject:
        return {
            "type": "error",
            "session_id": state["session_id"],
            "execution_id": execution_id,
            "message": "This approval belongs to a different actor.",
        }

    approval = workflow_store.claim_approval(approval_id, actor.subject, "rejected")
    if not approval:
        return {"type": "error", "message": "Approval not found or expired."}

    task_id = approval[
        "task_id"
    ]

    task = state[
        "tasks"
    ].get(
        task_id
    )

    if task:

        task["status"] = "cancelled"
        workflow_store.save_execution(state)

    log("")
    log("=" * 70)
    log("[APPROVAL] REJECTED")
    log("=" * 70)

    log(
        f"[APPROVAL] Task: {task_id}"
    )

    # --------------------------------------------------------
    # Block dependent tasks
    # --------------------------------------------------------

    blocked_count = 0

    for other_task in state[
        "tasks"
    ].values():

        if task_id in other_task.get(
            "depends_on",
            []
        ):

            if other_task["status"] == "pending":

                other_task[
                    "status"
                ] = "blocked"

                blocked_count += 1

                workflow_store.save_execution(state)

                log(
                    f"[EXECUTOR] BLOCKED: "
                    f"{other_task['id']}"
                )

    log(
        f"[EXECUTOR] Blocked dependent tasks: "
        f"{blocked_count}"
    )

    # --------------------------------------------------------
    # Continue independent tasks.
    # --------------------------------------------------------

    result = run_plan(
        execution_id
    )

    return result


# ============================================================
# PLAN SUMMARY
# ============================================================

def get_plan_summary(
    execution_id
):

    state = workflow_store.get_execution(execution_id)

    if not state:
        return None

    tasks = []

    for task in state[
        "tasks"
    ].values():

        tasks.append({
            "id": task["id"],
            "tool": task["tool"],
            "status": task["status"],
            "depends_on": task.get(
                "depends_on",
                []
            ),
            "requires_approval": task.get(
                "requires_approval",
                False
            ),
        })

    return {
        "execution_id": execution_id,
        "session_id": state["session_id"],
        "status": state["status"],
        "tasks": tasks,
    }


# ============================================================
# GET EXECUTION STATE
# ============================================================

def get_execution(
    execution_id
):

    return workflow_store.get_execution(execution_id)
