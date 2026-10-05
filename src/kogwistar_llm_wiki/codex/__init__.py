"""Public facade for Codex integration and backward-compatible memory names."""

from ..memory import (
    MemoryDisabledError,
    MemoryEvidence,
    MemoryRecord,
    MemoryService,
    MemoryValidationError,
)
from .codex_bridge import bridge_settings_from_environment, serve_codex_bridge
from .codex_workbench_agent import (
    CodexAppServerRunner,
    CodexCliCockpitResponder,
    CodexCliResponder,
    CodexCliSettings,
    HostCockpitResponder,
)

CodexMemoryError = MemoryValidationError
CodexMemoryRecord = MemoryRecord
CodexMemoryService = MemoryService

# Keep these names available to callers of the original Codex-facing facade.
__all__ = [
    "CodexAppServerRunner",
    "CodexCliCockpitResponder",
    "CodexCliResponder",
    "CodexCliSettings",
    "CodexMemoryError",
    "CodexMemoryRecord",
    "CodexMemoryService",
    "HostCockpitResponder",
    "MemoryDisabledError",
    "MemoryEvidence",
    "MemoryRecord",
    "MemoryService",
    "MemoryValidationError",
    "bridge_settings_from_environment",
    "serve_codex_bridge",
]
