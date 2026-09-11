"""Application-owned authentication and workspace authorization boundary.

The graph/runtime remains owned by Kogwistar.  This module only translates an
HTTP/MCP bearer credential into the claims context that Kogwistar already
understands and applies the llm-wiki workspace boundary before dispatch.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
import secrets
from typing import Iterator, Mapping

from kogwistar.server.auth_middleware import reset_claims_ctx, set_claims_ctx, verify_jwt


class IdentityError(ValueError):
    """A credential could not be authenticated or authorized."""

    def __init__(self, message: str, *, status: int) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class LlmWikiIdentity:
    principal_id: str
    scopes: frozenset[str]
    role: str
    security_scope: str
    workspaces: frozenset[str]
    claims: Mapping[str, object]
    auth_mode: str

    def can(self, scope: str) -> bool:
        return scope in self.scopes or "admin" in self.scopes or self.role == "rw" and scope == "write"


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def auth_mode() -> str:
    configured = os.getenv("LLM_WIKI_AUTH_MODE", "").strip().lower()
    if configured and configured not in {"disabled", "static_token", "kogwistar_jwt"}:
        raise IdentityError(
            "LLM_WIKI_AUTH_MODE must be disabled, static_token, or kogwistar_jwt",
            status=500,
        )
    if configured in {"disabled", "static_token", "kogwistar_jwt"}:
        return configured
    return "static_token" if (
        os.getenv("LLM_WIKI_API_TOKEN", "").strip()
        or os.getenv("LLM_WIKI_MCP_TOKEN", "").strip()
        or _truthy(os.getenv("LLM_WIKI_AUTH_REQUIRED", ""))
        or _truthy(os.getenv("LLM_WIKI_MCP_AUTH_REQUIRED", ""))
    ) else "disabled"


def _items(value: object, *, split_words: bool = False) -> set[str]:
    if isinstance(value, str):
        return {item.strip().lower() for item in (value.split() if split_words else value.split(",")) if item.strip()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return {str(item).strip().lower() for item in value if str(item).strip()}
    return set()


def _workspace_items(value: object) -> set[str]:
    """Keep workspace identifiers exact; only scope names are case-folded."""
    if isinstance(value, str):
        return {item.strip() for item in value.split(",") if item.strip()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return {str(item).strip() for item in value if str(item).strip()}
    return set()


def _claim_scopes(claims: Mapping[str, object]) -> frozenset[str]:
    values = _items(claims.get("scope"), split_words=True) | _items(claims.get("scp"), split_words=True)
    return frozenset(values)


def _configured_workspace_acl() -> dict[str, object]:
    raw = os.getenv("LLM_WIKI_WORKSPACE_ACL_JSON", "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise IdentityError("LLM_WIKI_WORKSPACE_ACL_JSON is not valid JSON", status=500) from exc
    if not isinstance(value, dict):
        raise IdentityError("LLM_WIKI_WORKSPACE_ACL_JSON must be an object", status=500)
    return value


def _workspace_permissions(identity: LlmWikiIdentity, workspace_id: str) -> set[str]:
    workspace = workspace_id.strip()
    if not workspace:
        raise IdentityError("workspace_id is required for this operation", status=400)
    acl = _configured_workspace_acl()
    principal = identity.principal_id
    entry = acl.get(principal, acl.get("*"))
    if isinstance(entry, dict) and isinstance(entry.get("workspaces"), dict):
        entry = entry["workspaces"]
    if isinstance(entry, dict):
        return _items(entry.get(workspace, entry.get("*")))
    if workspace in identity.workspaces or "*" in identity.workspaces:
        return set(identity.scopes)
    return set()


def authenticate_bearer(value: str | None) -> LlmWikiIdentity | None:
    """Authenticate a bearer token using static local auth or core JWT rules."""
    mode = auth_mode()
    if mode == "disabled":
        return None
    supplied = value or ""
    if supplied.lower().startswith("bearer "):
        supplied = supplied[7:].strip()
    if not supplied:
        raise IdentityError("bearer token is required", status=401)
    if mode == "static_token":
        api_expected = os.getenv("LLM_WIKI_API_TOKEN", "").strip()
        mcp_expected = os.getenv("LLM_WIKI_MCP_TOKEN", "").strip()
        matched_mcp = bool(mcp_expected and secrets.compare_digest(supplied, mcp_expected))
        matched_api = bool(api_expected and secrets.compare_digest(supplied, api_expected))
        if not matched_api and not matched_mcp:
            raise IdentityError("invalid bearer token", status=401)
        scopes = _items(
            (os.getenv("LLM_WIKI_MCP_TOKEN_SCOPES", "read,write") if matched_mcp else os.getenv("LLM_WIKI_API_TOKEN_SCOPES", "read,write"))
        )
        claims: dict[str, object] = {
            "sub": "llm-wiki-static-client",
            "role": "rw" if "write" in scopes else "ro",
            "scope": " ".join(sorted(scopes)),
            "security_scope": os.getenv("LLM_WIKI_SECURITY_SCOPE", "llm-wiki"),
        }
        return LlmWikiIdentity(claims["sub"], frozenset(scopes), str(claims["role"]), str(claims["security_scope"]), frozenset({"*"}), claims, mode)
    try:
        claims = verify_jwt(supplied, {
            "alg": os.getenv("JWT_ALG", "HS256"),
            "secret": os.getenv("JWT_SECRET"),
            "iss": os.getenv("JWT_ISS") or None,
            "aud": os.getenv("JWT_AUD") or None,
        })
    except Exception as exc:
        detail = getattr(exc, "detail", "invalid bearer token")
        status = int(getattr(exc, "status_code", 401) or 401)
        raise IdentityError(str(detail), status=status) from exc
    principal = str(claims.get("sub") or claims.get("client_id") or "").strip()
    if not principal:
        raise IdentityError("JWT must contain sub or client_id", status=401)
    scopes = _claim_scopes(claims)
    role = str(claims.get("role") or "ro").strip().lower()
    if role not in {"ro", "rw"}:
        role = "ro"
    workspaces = frozenset(
        _workspace_items(
            claims.get("workspaces")
            or claims.get("workspace_ids")
            or claims.get("allowed_workspaces")
        )
    )
    security_scope = str(claims.get("security_scope") or claims.get("tenant") or principal).strip().lower()
    normalized_claims = dict(claims)
    normalized_claims.setdefault("agent_id", principal)
    normalized_claims.setdefault("role", role)
    normalized_claims.setdefault("security_scope", security_scope)
    if "scope" not in normalized_claims and scopes:
        normalized_claims["scope"] = " ".join(sorted(scopes))
    return LlmWikiIdentity(principal, scopes, role, security_scope, workspaces, normalized_claims, mode)


def authorize(identity: LlmWikiIdentity | None, *, workspace_id: str | None, scope: str) -> None:
    if identity is None:
        return
    if not identity.can(scope):
        raise IdentityError(f"forbidden: scope '{scope}' is required", status=403)
    if workspace_id is None:
        return
    permissions = _workspace_permissions(identity, workspace_id)
    if scope not in permissions and "admin" not in permissions:
        raise IdentityError("forbidden: identity is not a member of this workspace", status=403)


@contextmanager
def claims_context(identity: LlmWikiIdentity | None) -> Iterator[None]:
    if identity is None:
        yield
        return
    token = set_claims_ctx(dict(identity.claims))
    try:
        yield
    finally:
        reset_claims_ctx(token)


__all__ = ["IdentityError", "LlmWikiIdentity", "auth_mode", "authenticate_bearer", "authorize", "claims_context"]
