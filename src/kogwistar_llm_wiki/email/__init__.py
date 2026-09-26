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
from .acceptance import EmailProposalMaterializer, EmailProposalPatch
from .review import (
    EmailReviewState,
    EmailReviewStateStore,
    InMemoryEmailReviewStateStore,
    SQLiteEmailReviewStateStore,
)

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
    "EmailProposalMaterializer",
    "EmailProposalPatch",
    "EmailReviewState",
    "EmailReviewStateStore",
    "InMemoryEmailEvidenceStore",
    "InMemoryEmailSyncStateStore",
    "SQLiteEmailEvidenceStore",
    "SQLiteEmailSyncStateStore",
    "InMemoryEmailReviewStateStore",
    "SQLiteEmailReviewStateStore",
]
