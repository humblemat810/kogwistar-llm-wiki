"""Compatibility facade for portable archive operations."""

from .archiving.archive_contracts import (
    ARCHIVE_FORMAT_VERSION,
    ArchiveError,
    ArchiveNamespace,
    RestoreReport,
)
from .archiving.io import assert_quiescent, workspace_archive_namespaces
from .archiving.operations import (
    create_archive,
    restore_archive,
    restore_backend_snapshot,
)
from .archiving.validation import inspect_archive, verify_archive

__all__ = [
    "ARCHIVE_FORMAT_VERSION",
    "ArchiveError",
    "ArchiveNamespace",
    "RestoreReport",
    "assert_quiescent",
    "create_archive",
    "inspect_archive",
    "restore_archive",
    "restore_backend_snapshot",
    "verify_archive",
    "workspace_archive_namespaces",
]
