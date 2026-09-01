"""Runtime boundaries for the packaged, local-only application."""

from .control import LocalStopEvent, stop_event_name
from .database_state import DatabaseClassification, DatabaseState, classify_database
from .diagnostics import DiagnosticLogger
from .manifest import (
    ManifestError,
    build_external_distribution_manifest,
    build_internal_manifest,
    validate_internal_manifest,
)
from .paths import RuntimePathError, RuntimePaths
from .settings import RuntimeSettings

__all__ = [
    "DatabaseClassification",
    "DatabaseState",
    "DiagnosticLogger",
    "LocalStopEvent",
    "ManifestError",
    "RuntimePathError",
    "RuntimePaths",
    "RuntimeSettings",
    "build_external_distribution_manifest",
    "build_internal_manifest",
    "classify_database",
    "stop_event_name",
    "validate_internal_manifest",
]
