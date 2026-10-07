import pytest

from engineering_registry.identity import (
    LocalStdioIdentityResolver,
    ResolvedIdentity,
    StaticTrustedIdentityResolver,
)
from engineering_registry.mcp_server import create_server
from engineering_registry.service import Principal, RegistryService
from engineering_registry.store import SQLiteGraphStore


def test_local_stdio_uses_host_user_fixed_agent_role_and_launcher_grants(monkeypatch):
    monkeypatch.setattr(
        "engineering_registry.identity.getpass.getuser", lambda: "os-user"
    )
    resolver = LocalStdioIdentityResolver({"P1"}, {"ORG1"})
    identity = resolver.resolve()
    assert identity.principal.actor_id == "os-user"
    assert identity.principal.role == "agent"
    assert identity.principal.project_ids == frozenset({"P1"})
    assert identity.principal.organization_ids == frozenset({"ORG1"})
    assert identity.principal.authentication_method == "local-stdio-os-user"
    assert "fixed by host" in identity.principal.authority_basis


def test_mcp_rejects_principal_that_differs_from_host_verified_identity(tmp_path):
    store = SQLiteGraphStore(tmp_path / "registry.sqlite3")
    try:
        principal = Principal("actual-user", frozenset({"P1"}), "agent")
        registry = RegistryService(store, principal)
        forged = ResolvedIdentity(
            Principal("forged-reviewer", frozenset({"P1"}), "reviewer"),
            "remote-token",
            "verified token claims",
        )
        with pytest.raises(PermissionError, match="trusted resolved identity"):
            create_server(
                registry, identity_resolver=StaticTrustedIdentityResolver(forged)
            )
    finally:
        store.close()


def test_local_mcp_cli_rejects_caller_selected_actor(capsys):
    from engineering_registry.mcp_server import main

    with pytest.raises(SystemExit) as error:
        main(["--database", "unused.sqlite3", "--project", "P", "--actor", "forged"])
    assert error.value.code == 2
    assert "unrecognized arguments: --actor" in capsys.readouterr().err


def test_audit_events_record_trusted_actor_role_and_basis(tmp_path):
    store = SQLiteGraphStore(tmp_path / "registry.sqlite3")
    try:
        identity = LocalStdioIdentityResolver({"P1"}).resolve()
        registry = RegistryService(store, identity.principal)
        registry._event("P1", "R1", "test_event")
        event = registry.history("P1", "R1")[0]
        assert event["actor_id"] == identity.principal.actor_id
        assert event["actor_role"] == "agent"
        assert event["authentication_method"] == "local-stdio-os-user"
        assert event["authority_basis"] == identity.authority_basis
    finally:
        store.close()
