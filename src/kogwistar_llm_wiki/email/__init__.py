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
from .sync import (
    EmailSourceAdapter,
    EmailSyncResult,
    EmailSyncService,
    EmailSyncStateStore,
    InMemoryEmailSyncStateStore,
    SQLiteEmailSyncStateStore,
)
from .ontology import EmailOntologyBinding

__all__ = [
    "EmailEvidenceRecord",
    "EmailEvidenceStore",
    "EmailIngestRequest",
    "EmailIngestResult",
    "EmailPluginUnavailable",
    "EmailRuntime",
    "EmailViewer",
    "EmailSourceAdapter",
    "EmailSyncResult",
    "EmailSyncService",
    "EmailSyncStateStore",
    "EmailOntologyBinding",
    "InMemoryEmailEvidenceStore",
    "InMemoryEmailSyncStateStore",
    "SQLiteEmailEvidenceStore",
    "SQLiteEmailSyncStateStore",
]
