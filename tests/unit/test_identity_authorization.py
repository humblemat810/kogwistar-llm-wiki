from __future__ import annotations

import json

import pytest
from jose import jwt

from kogwistar.server.auth_middleware import claims_ctx
from kogwistar_llm_wiki.identity import (
    IdentityError,
    authenticate_bearer,
    authorize,
    claims_context,
)


def _token(**claims: object) -> str:
    payload = {"sub": "alice", "scope": "read write", "workspaces": ["team-a"], **claims}
    return jwt.encode(payload, "test-secret", algorithm="HS256")


def test_jwt_identity_reuses_core_verifier_and_claims(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_WIKI_AUTH_MODE", "kogwistar_jwt")
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    monkeypatch.setenv("JWT_ALG", "HS256")
    identity = authenticate_bearer("Bearer " + _token())
    assert identity is not None
    assert identity.principal_id == "alice"
    assert identity.scopes == {"read", "write"}
    assert identity.workspaces == {"team-a"}
    authorize(identity, workspace_id="team-a", scope="read")
    with pytest.raises(IdentityError, match="not a member"):
        authorize(identity, workspace_id="team-b", scope="read")


def test_explicit_workspace_acl_can_grant_without_workspace_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_WIKI_AUTH_MODE", "kogwistar_jwt")
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    monkeypatch.setenv("LLM_WIKI_WORKSPACE_ACL_JSON", json.dumps({"alice": {"workspaces": {"team-b": ["read"]}}}))
    identity = authenticate_bearer("Bearer " + _token(workspaces=[]))
    assert identity is not None
    authorize(identity, workspace_id="team-b", scope="read")
    with pytest.raises(IdentityError):
        authorize(identity, workspace_id="team-b", scope="write")


def test_invalid_jwt_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_WIKI_AUTH_MODE", "kogwistar_jwt")
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    with pytest.raises(IdentityError) as error:
        authenticate_bearer("Bearer not-a-token")
    assert error.value.status == 401


def test_core_claims_context_is_reset_after_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_WIKI_AUTH_MODE", "kogwistar_jwt")
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    identity = authenticate_bearer("Bearer " + _token())
    assert claims_ctx.get() is None
    with claims_context(identity):
        assert claims_ctx.get()["sub"] == "alice"
    assert claims_ctx.get() is None


def test_static_mode_remains_local_compatible(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_WIKI_AUTH_MODE", "static_token")
    monkeypatch.setenv("LLM_WIKI_API_TOKEN", "secret")
    identity = authenticate_bearer("Bearer secret")
    assert identity is not None
    authorize(identity, workspace_id="any-local-workspace", scope="write")


def test_explicit_disabled_mode_has_no_identity_or_acl_requirement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_WIKI_AUTH_MODE", "disabled")
    monkeypatch.setenv("LLM_WIKI_API_TOKEN", "leftover-token")
    monkeypatch.setenv("LLM_WIKI_AUTH_REQUIRED", "true")
    identity = authenticate_bearer(None)
    assert identity is None
    authorize(identity, workspace_id="personal", scope="write")


def test_invalid_auth_mode_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_WIKI_AUTH_MODE", "jwtish")
    with pytest.raises(IdentityError, match="must be disabled") as error:
        authenticate_bearer(None)
    assert error.value.status == 500


def test_workspace_authorization_does_not_change_case(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_WIKI_AUTH_MODE", "kogwistar_jwt")
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    identity = authenticate_bearer("Bearer " + _token(workspaces=["Team-A"]))
    assert identity is not None
    authorize(identity, workspace_id="Team-A", scope="read")
    with pytest.raises(IdentityError, match="not a member"):
        authorize(identity, workspace_id="team-a", scope="read")
