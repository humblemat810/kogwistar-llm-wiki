"""Typed boundary for explicitly enabled trusted workbench extensions.

Extensions are installed application code, not marketplace tools. The host owns
authentication and enforces each route's declared scope before dispatch.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points
from typing import Literal, Protocol

from kogwistar.json_types import JsonObject, JsonValue

from ..configuration.identity import LlmWikiIdentity

ExtensionMethod = Literal["GET", "POST"]
ExtensionScope = Literal["read", "write"]
ExtensionContentType = Literal["application/json", "text/html"]

class WorkspaceIdResolver(Protocol):
    """Resolve the workspace selected by an extension request."""

    def __call__(
        self,
        query: Mapping[str, tuple[str, ...]],
        payload: JsonObject,
        /,
    ) -> str | None: ...

_EXTENSION_ID = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
WORKBENCH_EXTENSION_ENTRY_POINT_GROUP = "kogwistar_llm_wiki.workbench_extensions"


@dataclass(frozen=True, slots=True)
class WorkbenchExtensionRequest:
    method: ExtensionMethod
    path: str
    query: Mapping[str, tuple[str, ...]]
    payload: JsonObject
    workspace_id: str | None
    identity: LlmWikiIdentity | None


@dataclass(frozen=True, slots=True)
class WorkbenchExtensionResponse:
    body: JsonValue
    status: int = 200
    content_type: ExtensionContentType = "application/json"

    def __post_init__(self) -> None:
        if type(self.status) is not int or not 200 <= self.status <= 599:
            raise ValueError("extension response status must be between 200 and 599")
        if self.content_type not in {"application/json", "text/html"}:
            raise ValueError("extension response content type is unsupported")
        if self.content_type == "text/html" and not isinstance(self.body, str):
            raise TypeError("HTML extension responses must contain a string body")


class ExtensionHandler(Protocol):
    """Handle one already-authenticated extension request."""

    def __call__(self, request: WorkbenchExtensionRequest, /) -> WorkbenchExtensionResponse: ...


@dataclass(frozen=True, slots=True)
class WorkbenchExtensionRoute:
    method: ExtensionMethod
    path: str
    required_scope: ExtensionScope
    workspace_id: WorkspaceIdResolver
    handler: ExtensionHandler

    def __post_init__(self) -> None:
        if self.method not in {"GET", "POST"}:
            raise ValueError("extension route method must be GET or POST")
        if self.required_scope not in {"read", "write"}:
            raise ValueError("extension route scope must be read or write")
        if not callable(self.workspace_id) or not callable(self.handler):
            raise TypeError("extension route requires workspace and handler callables")


@dataclass(frozen=True, slots=True)
class WorkbenchExtension:
    extension_id: str
    routes: Sequence[WorkbenchExtensionRoute]

    def __post_init__(self) -> None:
        object.__setattr__(self, "routes", tuple(self.routes))
        if not _EXTENSION_ID.fullmatch(self.extension_id):
            raise ValueError("extension_id must be a lowercase URL-safe identifier")
        prefix = f"/plugins/{self.extension_id}/"
        seen: set[tuple[str, str]] = set()
        for route in self.routes:
            if (
                not route.path.startswith(prefix)
                or "?" in route.path
                or "#" in route.path
                or "//" in route.path
                or ".." in route.path.split("/")
            ):
                raise ValueError(f"extension routes must be namespaced under {prefix}")
            key = (route.method, route.path)
            if key in seen:
                raise ValueError(f"duplicate extension route: {route.method} {route.path}")
            seen.add(key)


class WorkbenchExtensionFactory(Protocol):
    """Factory for trusted extension code, invoked only after explicit enablement."""

    def __call__(self, host: object) -> WorkbenchExtension: ...


def index_workbench_extensions(
    extensions: Sequence[WorkbenchExtension],
) -> dict[tuple[str, str], WorkbenchExtensionRoute]:
    """Validate and index route declarations before opening the HTTP listener."""
    indexed: dict[tuple[str, str], WorkbenchExtensionRoute] = {}
    extension_ids: set[str] = set()
    for extension in extensions:
        if extension.extension_id in extension_ids:
            raise ValueError(f"duplicate workbench extension: {extension.extension_id}")
        extension_ids.add(extension.extension_id)
        for route in extension.routes:
            key = (route.method, route.path)
            if key in indexed:
                raise ValueError(f"duplicate workbench extension route: {route.method} {route.path}")
            indexed[key] = route
    return indexed


def load_workbench_extensions(
    host: object,
    enabled_ids: Sequence[str],
) -> tuple[WorkbenchExtension, ...]:
    """Load only explicitly enabled, installed application extensions.

    Installed entry points are executable trusted code. Merely installing a
    package never activates it; callers must supply an explicit allowlist.
    """
    requested = tuple(enabled_ids)
    if len(set(requested)) != len(requested):
        raise ValueError("workbench extension allowlist contains duplicate ids")
    if any(not _EXTENSION_ID.fullmatch(item) for item in requested):
        raise ValueError("workbench extension allowlist contains an invalid id")
    if not requested:
        return ()

    requested_set = set(requested)
    available: dict[str, EntryPoint] = {}
    for point in entry_points(group=WORKBENCH_EXTENSION_ENTRY_POINT_GROUP):
        if point.name not in requested_set:
            continue
        if point.name in available:
            raise RuntimeError(f"duplicate installed workbench extension entry point: {point.name}")
        available[point.name] = point

    missing = tuple(item for item in requested if item not in available)
    if missing:
        raise RuntimeError(
            "enabled workbench extensions are not installed: " + ", ".join(missing)
        )

    loaded: list[WorkbenchExtension] = []
    for extension_id in requested:
        point = available[extension_id]
        try:
            factory = point.load()
            extension = factory(host)
        except Exception as exc:
            raise RuntimeError(
                f"failed to initialize enabled workbench extension: {extension_id}"
            ) from exc
        if not isinstance(extension, WorkbenchExtension):
            raise TypeError(
                f"workbench extension factory {extension_id!r} returned an invalid object"
            )
        if extension.extension_id != extension_id:
            raise ValueError(
                f"workbench extension id mismatch: configured {extension_id!r}, "
                f"factory returned {extension.extension_id!r}"
            )
        loaded.append(extension)

    index_workbench_extensions(loaded)
    return tuple(loaded)


__all__ = [
    "WORKBENCH_EXTENSION_ENTRY_POINT_GROUP",
    "ExtensionContentType",
    "ExtensionMethod",
    "ExtensionScope",
    "ExtensionHandler",
    "WorkspaceIdResolver",
    "WorkbenchExtension",
    "WorkbenchExtensionFactory",
    "WorkbenchExtensionRequest",
    "WorkbenchExtensionResponse",
    "WorkbenchExtensionRoute",
    "index_workbench_extensions",
    "load_workbench_extensions",
]
