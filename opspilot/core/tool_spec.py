from dataclasses import dataclass
from typing import Any, Callable, Literal

from .audit_log import redact
from .models import CommandResult
from ..tools.ai import generate_reply, summarize_email, summarize_thread
from ..tools.licenses import assign_license, list_available_licenses, list_user_licenses
from ..tools.mail import (
    draft_email,
    get_email,
    list_recent_emails,
    list_unread_emails,
    reply_email,
    search_emails,
    send_email,
)
from ..tools.users import (
    create_user,
    delete_user,
    disable_user,
    enable_user,
    get_user,
    get_account_report,
    list_users,
    reset_password,
    revoke_sessions,
)


SideEffect = Literal["read", "draft", "write"]
Handler = Callable[[dict[str, Any]], Any]


def _is_reference(value: Any) -> bool:
    return isinstance(value, dict) and "$ref" in value or (
        isinstance(value, str) and "{{" in value and "}}" in value
    )


def _validate_fields(required: tuple[str, ...]) -> Callable[[dict[str, Any], bool], None]:
    def validate(parameters: dict[str, Any], allow_references: bool = True) -> None:
        if not isinstance(parameters, dict):
            raise ValueError("Tool parameters must be an object.")
        for field in required:
            if field not in parameters or parameters[field] is None:
                raise ValueError(f"Missing required parameter '{field}'.")
            value = parameters[field]
            if not allow_references and not isinstance(value, str):
                raise ValueError(f"Parameter '{field}' must resolve to a string.")
            if isinstance(value, str) and not value.strip() and not _is_reference(value):
                raise ValueError(f"Parameter '{field}' cannot be empty.")
    return validate


def _validate_license(parameters: dict[str, Any], allow_references: bool = True) -> None:
    _validate_fields(("license",))(parameters, allow_references)
    if not any(field in parameters for field in ("user", "user_id", "display_name")):
        raise ValueError("License operations require user, user_id, or display_name.")


def _validate_user_selector(parameters: dict[str, Any], allow_references: bool = True) -> None:
    if not isinstance(parameters, dict):
        raise ValueError("Tool parameters must be an object.")
    if not any(field in parameters for field in ("user", "user_id", "display_name")):
        raise ValueError("Operation requires user, user_id, or display_name.")


def _validate_account_report(parameters: dict[str, Any], allow_references: bool = True) -> None:
    if not isinstance(parameters, dict):
        raise ValueError("Tool parameters must be an object.")
    if not any(parameters.get(field) for field in ("user", "department")):
        raise ValueError("Account reports require a user or department.")
    if parameters.get("user") and parameters.get("department"):
        raise ValueError("Account reports accept either a user or department, not both.")


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    handler: Handler
    side_effect: SideEffect
    input_validator: Callable[[dict[str, Any], bool], None]
    output_fields: frozenset[str]

    @property
    def requires_approval(self) -> bool:
        # A draft changes the mailbox even though it does not send mail.
        # The operator must review its preview before that change is made.
        return self.side_effect in {"write", "draft"}

    def validate_input(self, parameters: dict[str, Any], allow_references: bool = True) -> None:
        self.input_validator(parameters, allow_references)

    def normalize_result(self, value: Any) -> dict[str, Any]:
        result = CommandResult.from_legacy(value)
        if result.success and not result.public_summary:
            result.public_summary = f"{self.description} completed."
        return result.to_dict()

    def approval_summary(self, parameters: dict[str, Any]) -> dict[str, Any]:
        return {
            "tool": self.name,
            "parameters": redact(parameters),
            "side_effect": self.side_effect,
        }


def _spec(
    name: str,
    description: str,
    handler: Handler,
    side_effect: SideEffect,
    required: tuple[str, ...] = (),
    output_fields: tuple[str, ...] = (),
    validator: Callable[[dict[str, Any], bool], None] | None = None,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description,
        handler=handler,
        side_effect=side_effect,
        input_validator=validator or _validate_fields(required),
        output_fields=frozenset(output_fields),
    )


TOOL_SPECS = {
    spec.name: spec
    for spec in (
        _spec("list_available_licenses", "List tenant licenses", list_available_licenses, "read", output_fields=("licenses",)),
        _spec("create_user", "Create a Microsoft 365 user", create_user, "write", ("first_name", "last_name", "department"), ("user_id", "display_name", "company_email", "personal_email", "department", "temporary_password")),
        _spec("send_email", "Send an email", send_email, "write", ("recipient", "subject", "body")),
        _spec("summarize_thread", "Summarize an email thread", summarize_thread, "read", ("thread",), ("summary",)),
        _spec("generate_reply", "Generate an email reply", generate_reply, "read", ("email",), ("reply",)),
        _spec("summarize_email", "Summarize an email", summarize_email, "read", ("email_id",), ("summary",)),
        _spec("draft_email", "Create an email draft", draft_email, "draft", ("recipient", "subject", "body"), ("draft_id",)),
        _spec("reply_email", "Reply to an email", reply_email, "write", ("email_id", "body")),
        _spec("get_email", "Get an email", get_email, "read", ("email_id",), ("email",)),
        _spec("list_unread_emails", "List unread emails", list_unread_emails, "read", output_fields=("emails",)),
        _spec("list_recent_emails", "List recent emails", list_recent_emails, "read", output_fields=("emails",)),
        _spec("get_user", "Get a user", get_user, "read", ("user",), ("data",)),
        _spec("get_account_report", "Get a user account report", get_account_report, "read", output_fields=("reports",), validator=_validate_account_report),
        _spec("list_users", "List users", list_users, "read", output_fields=("users",)),
        _spec("assign_license", "Assign a license", assign_license, "write", output_fields=("user_id", "license", "sku_id"), validator=_validate_license),
        _spec("disable_user", "Disable a user", disable_user, "write", ("user",)),
        _spec("enable_user", "Enable a user", enable_user, "write", ("user",)),
        _spec("reset_password", "Reset a user password", reset_password, "write", ("user",), ("temporary_password",)),
        _spec("revoke_sessions", "Revoke user sessions", revoke_sessions, "write", ("user",)),
        _spec("delete_user", "Delete a user", delete_user, "write", ("user",)),
        _spec("list_user_licenses", "List user licenses", list_user_licenses, "read", validator=_validate_user_selector, output_fields=("user_id", "user", "licenses")),
        _spec("search_emails", "Search emails", search_emails, "read", ("query",), ("emails",)),
    )
}


def get_tool_spec(name: str) -> ToolSpec:
    try:
        return TOOL_SPECS[name]
    except KeyError as exc:
        raise ValueError(f"Unknown tool: {name}") from exc
