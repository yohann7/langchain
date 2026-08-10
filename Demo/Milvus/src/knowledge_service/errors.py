"""Stable domain errors used across transport and storage boundaries."""


class IdempotencyConflict(ValueError):
    """A request id was reused for a different ingestion payload."""


class KnowledgeUnavailableError(RuntimeError):
    """A required Knowledge backend is unavailable."""


class IngestionDeniedError(PermissionError):
    """A document failed an ingestion security policy."""


class UnsupportedDocumentError(ValueError):
    """The configured parser registry cannot read a document."""
