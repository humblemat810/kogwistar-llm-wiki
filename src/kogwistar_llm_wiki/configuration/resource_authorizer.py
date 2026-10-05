"""Load an explicitly configured application resource-ACL adapter."""

from __future__ import annotations

import importlib
import os
from typing import Protocol, cast


class ResourceAuthorizer(Protocol):
    """Authorize one resource operation before graph access."""

    def __call__(
        self,
        workspace_id: str,
        resource_kind: str,
        resource_id: str,
        operation: str,
        /,
    ) -> bool: ...


def load_resource_authorizer(
    specification: str | None = None,
) -> ResourceAuthorizer | None:
    """Resolve ``module:callable`` from explicit config; never infer grants.

    The callback runs inside Kogwistar's request claims context for HTTP/MCP
    requests, so application policy can bind decisions to the authenticated
    principal. Missing configuration intentionally returns ``None``.
    """
    configured = (
        os.environ.get("LLM_WIKI_RESOURCE_AUTHORIZER", "")
        if specification is None
        else specification
    ).strip()
    if not configured:
        return None
    module_name, separator, attribute = configured.partition(":")
    if not separator or not module_name.strip() or not attribute.strip() or ":" in attribute:
        raise ValueError("LLM_WIKI_RESOURCE_AUTHORIZER must be module:callable")
    try:
        module = importlib.import_module(module_name.strip())
    except Exception as exc:
        raise RuntimeError("configured resource ACL adapter could not be imported") from exc
    authorizer = getattr(module, attribute.strip(), None)
    if not callable(authorizer):
        raise TypeError("configured resource ACL adapter must name a callable")
    return cast(ResourceAuthorizer, authorizer)


__all__ = ["ResourceAuthorizer", "load_resource_authorizer"]
