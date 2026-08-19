from dataclasses import dataclass, field
from typing import FrozenSet


@dataclass(frozen=True)
class Actor:
    """The caller boundary used by orchestration code.

    Phase 1 deliberately does not authenticate this identity. HTTP authentication
    and authorization are introduced only when the tenant issuer/audience and
    role mapping are known. The explicit object prevents future code from
    treating an anonymous request as an implicit authority.
    """

    subject: str
    tenant_id: str | None = None
    display_name: str | None = None
    roles: FrozenSet[str] = field(default_factory=frozenset)

    @classmethod
    def local(cls) -> "Actor":
        """Compatibility actor for the existing local UI and CLI."""
        return cls(subject="local-development", display_name="Local development")
