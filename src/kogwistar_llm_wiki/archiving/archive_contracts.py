from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ARCHIVE_FORMAT_VERSION = 2
READABLE_ARCHIVE_FORMATS = {1, ARCHIVE_FORMAT_VERSION}
ARTIFACT_DIRS = ("raw_documents", "source_artifacts", "parser_runs", "workflow_runs", "maintenance")
SECRET_NAMES = {".env", ".env.local", "auth.sqlite", "credentials.json", "secrets.json"}


class ArchiveError(RuntimeError):
    """Raised when an archive cannot be safely created, verified, or restored."""


@dataclass(frozen=True, slots=True)
class ArchiveNamespace:
    label: str
    engine: Any
    namespace: str


@dataclass(frozen=True, slots=True)
class RestoreReport:
    archive_id: str
    workspace_id: str
    target_workspace_id: str
    dry_run: bool
    imported_events: int
    replayed_namespaces: int
    rebuilt_vectors: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "archive_id": self.archive_id,
            "workspace_id": self.workspace_id,
            "target_workspace_id": self.target_workspace_id,
            "dry_run": self.dry_run,
            "imported_events": self.imported_events,
            "replayed_namespaces": self.replayed_namespaces,
            "rebuilt_vectors": self.rebuilt_vectors,
        }
