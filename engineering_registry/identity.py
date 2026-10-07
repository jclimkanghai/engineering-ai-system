"""Trusted identity resolution seams for local and future remote MCP hosts."""

from __future__ import annotations

import getpass
from dataclasses import dataclass
from typing import Protocol

from .service import Principal


@dataclass(frozen=True)
class ResolvedIdentity:
    principal: Principal
    method: str
    authority_basis: str


class IdentityResolver(Protocol):
    def resolve(self) -> ResolvedIdentity: ...


class LocalStdioIdentityResolver:
    """Resolve local OS identity plus grants fixed by the trusted MCP launcher."""

    def __init__(
        self, project_grants: set[str], organization_grants: set[str] | None = None
    ):
        if not project_grants:
            raise ValueError(
                "Trusted launcher must configure at least one project grant"
            )
        self.project_grants = frozenset(project_grants)
        self.organization_grants = frozenset(organization_grants or set())

    def resolve(self) -> ResolvedIdentity:
        actor = getpass.getuser()
        basis = "trusted launcher project/organisation grants; agent role fixed by host"
        return ResolvedIdentity(
            Principal(
                actor,
                self.project_grants,
                "agent",
                self.organization_grants,
                "local-stdio-os-user",
                basis,
            ),
            "local-stdio-os-user",
            basis,
        )


class StaticTrustedIdentityResolver:
    """Host/test adapter for identity already verified outside MCP arguments."""

    def __init__(self, identity: ResolvedIdentity):
        self._identity = identity

    def resolve(self) -> ResolvedIdentity:
        return self._identity
