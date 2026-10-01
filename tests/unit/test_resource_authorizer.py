from __future__ import annotations

import sys
from types import ModuleType

import pytest
from kogwistar.server.auth_middleware import claims_ctx

from kogwistar_llm_wiki import build_in_memory_namespace_engines
from kogwistar_llm_wiki.configuration.identity import LlmWikiIdentity, claims_context
from kogwistar_llm_wiki.configuration.resource_authorizer import (
    load_resource_authorizer,
)
from kogwistar_llm_wiki.ingest_pipeline import IngestPipeline
from kogwistar_llm_wiki.workbench.workbench_api import WorkbenchApi


@pytest.mark.ci
def test_resource_authorizer_is_explicit_and_uses_authenticated_claim_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert load_resource_authorizer("") is None

    module = ModuleType("test_application_resource_acl")
    def authorize(workspace, kind, resource, action):
        claims = claims_ctx.get()
        return bool(
            claims
            and claims.get("sub") == "alice"
            and (workspace, kind, resource, action)
            == ("workspace-a", "source_stream", "stream-a", "read")
        )

    module.authorize = authorize
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setenv(
        "LLM_WIKI_RESOURCE_AUTHORIZER", f"{module.__name__}:authorize"
    )
    authorizer = load_resource_authorizer()
    assert authorizer is module.authorize

    engines = build_in_memory_namespace_engines()
    try:
        api = WorkbenchApi(
            IngestPipeline(engines),
            resource_authorizer=authorizer,
        )
        alice = LlmWikiIdentity(
            principal_id="alice",
            scopes=frozenset({"read"}),
            role="ro",
            security_scope="team-a",
            workspaces=frozenset({"workspace-a"}),
            claims={"sub": "alice", "scope": "read"},
            auth_mode="test",
        )
        bob = LlmWikiIdentity(
            principal_id="bob",
            scopes=frozenset({"read"}),
            role="ro",
            security_scope="team-a",
            workspaces=frozenset({"workspace-a"}),
            claims={"sub": "bob", "scope": "read"},
            auth_mode="test",
        )
        assert api.authorize_resource(
            "workspace-a", "source_stream", "stream-a", "read"
        ) is False
        with claims_context(alice):
            assert api.authorize_resource(
                "workspace-a", "source_stream", "stream-a", "read"
            ) is True
        with claims_context(bob):
            assert api.authorize_resource(
                "workspace-a", "source_stream", "stream-a", "read"
            ) is False
    finally:
        engines.close()


@pytest.mark.ci
def test_resource_authorizer_configuration_rejects_invalid_specs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_WIKI_RESOURCE_AUTHORIZER", "not-a-reference")
    with pytest.raises(ValueError, match="module:callable"):
        load_resource_authorizer()

    module = ModuleType("test_non_callable_resource_acl")
    module.authorize = False
    monkeypatch.setitem(sys.modules, module.__name__, module)
    with pytest.raises(TypeError, match="must name a callable"):
        load_resource_authorizer(f"{module.__name__}:authorize")
