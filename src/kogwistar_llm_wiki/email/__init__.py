"""Optional email-plugin integration for LLM-Wiki."""

from .runtime import (
    EmailEvidenceRecord,
    EmailEvidenceStore,
    EmailIngestRequest,
    EmailIngestResult,
    EmailPluginUnavailable,
    EmailRuntime,
    InMemoryEmailEvidenceStore,
    SQLiteEmailEvidenceStore,
)
from .viewer import EmailViewer

__all__ = [
    "EmailEvidenceRecord",
    "EmailEvidenceStore",
    "EmailIngestRequest",
    "EmailIngestResult",
    "EmailPluginUnavailable",
    "EmailRuntime",
    "EmailViewer",
    "InMemoryEmailEvidenceStore",
    "SQLiteEmailEvidenceStore",
]
