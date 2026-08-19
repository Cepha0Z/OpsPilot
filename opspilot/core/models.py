from dataclasses import dataclass, field
import re
from typing import Any, Literal


ResultStatus = Literal["success", "failed"]
ExecutionOutcome = Literal[
    "completed",
    "partially_completed",
    "cancelled",
    "failed",
    "approval_required",
    "error",
]


_SENSITIVE_TEXT_PATTERN = re.compile(
    r"(?i)\b((?:verification|security|one[- ]time|otp|passcode|activation|code)"
    r"(?:\s+(?:code|password|pin))?(?:\s+is)?\s*[:\-]?\s*)\d{4,8}\b"
)
_SENSITIVE_SECRET_PATTERN = re.compile(
    r"\b(password|passcode|api key|client secret|access token|authentication token|secret)"
    r"\s*(?:is|:|=)\s*[^\s,;.!]+",
    re.IGNORECASE,
)


def sanitize_public_text(value: Any) -> str:
    """Keep user-facing summaries useful without disclosing secrets."""
    text = _SENSITIVE_TEXT_PATTERN.sub(r"\1[REDACTED]", str(value))
    return _SENSITIVE_SECRET_PATTERN.sub(r"\1 [REDACTED]", text)


@dataclass
class Task:
    id: str
    tool: str
    parameters: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    requires_approval: bool = False

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Task":
        return cls(
            id=value["id"],
            tool=value["tool"],
            parameters=value.get("parameters", {}),
            depends_on=value.get("depends_on", []),
            requires_approval=value.get("requires_approval", False),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tool": self.tool,
            "parameters": self.parameters,
            "depends_on": self.depends_on,
            "requires_approval": self.requires_approval,
        }


@dataclass
class Plan:
    type: Literal["plan"]
    tasks: list[Task]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Plan":
        if value.get("type") != "plan":
            raise ValueError("Expected a complete plan.")
        return cls(type="plan", tasks=[Task.from_dict(task) for task in value.get("tasks", [])])

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "tasks": [task.to_dict() for task in self.tasks]}


@dataclass
class CommandResult:
    """Normalized internal result returned by every registered tool."""

    status: ResultStatus
    data: dict[str, Any] = field(default_factory=dict)
    private_data: dict[str, Any] = field(default_factory=dict)
    public_summary: str = ""
    error_code: str | None = None
    retryable: bool = False

    @property
    def success(self) -> bool:
        return self.status == "success"

    @classmethod
    def from_legacy(cls, value: Any) -> "CommandResult":
        if not isinstance(value, dict):
            return cls(status="success", data={"value": value}, public_summary=str(value))

        success = value.get("success", True) is not False
        summary = sanitize_public_text(value.get("message", ""))
        data = {
            key: item
            for key, item in value.items()
            if key not in {"success", "message", "error", "error_code", "retryable"}
        }

        # Private values remain available only to a dependent executor task;
        # they are never a public result and logging redacts them.
        private_data = {}
        for key in ("temporary_password", "password", "access_token"):
            if key in data:
                private_data[key] = data.pop(key)
        data.pop("graph_response", None)

        return cls(
            status="success" if success else "failed",
            data=data,
            private_data=private_data,
            public_summary=summary,
            error_code=value.get("error_code") or ("tool_failed" if not success else None),
            retryable=bool(value.get("retryable", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "status": self.status,
            "data": self.data,
            "private_data": self.private_data,
            "public_summary": sanitize_public_text(self.public_summary),
            "error_code": self.error_code,
            "retryable": self.retryable,
        }
