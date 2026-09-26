"""Optional email-plugin integration for LLM-Wiki."""

from .acceptance import EmailProposalMaterializer, EmailProposalPatch
from .bindings import (
    EmailBindingConflict,
    EmailBindingNotFound,
    EmailConnectorBinding,
    EmailConnectorBindingStore,
    InMemoryEmailConnectorBindingStore,
    SQLiteEmailConnectorBindingStore,
)
from .catalog import (
    EmailCatalogAuthorizer,
    EmailCatalogSemanticRanker,
    EmailOntologyCatalog,
    EmailOntologySearchHit,
    EmailOntologySemanticProjection,
    EmailOntologyTextEmbedder,
)
from .jobs import (
    AdapterFactory,
    EmailSyncJobOutcome,
    EmailSyncJobRequest,
    EmailSyncJobScheduler,
)
from .leases import (
    EmailSyncLease,
    EmailSyncLeaseConflict,
    EmailSyncLeaseLost,
    EmailSyncLeaseStore,
    InMemoryEmailSyncLeaseStore,
    SQLiteEmailSyncLeaseStore,
)
from .memory import EmailMemoryPromotion, EmailMemoryPromotionService
from .ontology import EmailOntologyBinding
from .proposals import (
    EmailMappingProposal,
    EmailMappingProposalStore,
    InMemoryEmailMappingProposalStore,
    SQLiteEmailMappingProposalStore,
)
from .review import (
    EmailReviewState,
    EmailReviewStateStore,
    InMemoryEmailReviewStateStore,
    SQLiteEmailReviewStateStore,
)
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
from .sync import (
    EmailMailboxEvent,
    EmailMailboxEventStore,
    EmailSourceAdapter,
    EmailSyncResult,
    EmailSyncService,
    EmailSyncStateStore,
    InMemoryEmailMailboxEventStore,
    InMemoryEmailSyncStateStore,
    SQLiteEmailMailboxEventStore,
    SQLiteEmailSyncStateStore,
)
from .viewer import EmailViewer
from .viewer_plugin import render_email_viewer_plugin

__all__ = [
    "AdapterFactory",
    "EmailBindingConflict",
    "EmailBindingNotFound",
    "EmailCatalogAuthorizer",
    "EmailCatalogSemanticRanker",
    "EmailConnectorBinding",
    "EmailConnectorBindingStore",
    "EmailEvidenceRecord",
    "EmailEvidenceStore",
    "EmailIngestRequest",
    "EmailIngestResult",
    "EmailMailboxEvent",
    "EmailMailboxEventStore",
    "EmailMappingProposal",
    "EmailMappingProposalStore",
    "EmailMemoryPromotion",
    "EmailMemoryPromotionService",
    "EmailOntologyBinding",
    "EmailOntologyCatalog",
    "EmailOntologySearchHit",
    "EmailOntologySemanticProjection",
    "EmailOntologyTextEmbedder",
    "EmailPluginUnavailable",
    "EmailProposalMaterializer",
    "EmailProposalPatch",
    "EmailReviewState",
    "EmailReviewStateStore",
    "EmailRuntime",
    "EmailSourceAdapter",
    "EmailSyncJobOutcome",
    "EmailSyncJobRequest",
    "EmailSyncJobScheduler",
    "EmailSyncLease",
    "EmailSyncLeaseConflict",
    "EmailSyncLeaseLost",
    "EmailSyncLeaseStore",
    "EmailSyncResult",
    "EmailSyncService",
    "EmailSyncStateStore",
    "EmailViewer",
    "InMemoryEmailConnectorBindingStore",
    "InMemoryEmailEvidenceStore",
    "InMemoryEmailMailboxEventStore",
    "InMemoryEmailMappingProposalStore",
    "InMemoryEmailReviewStateStore",
    "InMemoryEmailSyncLeaseStore",
    "InMemoryEmailSyncStateStore",
    "SQLiteEmailConnectorBindingStore",
    "SQLiteEmailEvidenceStore",
    "SQLiteEmailMailboxEventStore",
    "SQLiteEmailMappingProposalStore",
    "SQLiteEmailReviewStateStore",
    "SQLiteEmailSyncLeaseStore",
    "SQLiteEmailSyncStateStore",
    "render_email_viewer_plugin",
]
