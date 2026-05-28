"""Core module — pure models, protocols, and configuration."""

from .config import CleanConfig
from .errors import (
    CleanError,
    SecurityError,
    PathTraversalError,
    InputValidationError,
    IndexingError,
    SearchError,
    StorageError,
    ParsingError,
)
from .models import CodeEntity, FileState, ProjectState, SearchResult, SearchContext
from .types import EntityKind, Language, IndexPhase

__all__ = [
    "CleanConfig",
    "CleanError",
    "SecurityError",
    "PathTraversalError",
    "InputValidationError",
    "IndexingError",
    "SearchError",
    "StorageError",
    "ParsingError",
    "CodeEntity",
    "FileState",
    "ProjectState",
    "SearchResult",
    "SearchContext",
    "EntityKind",
    "Language",
    "IndexPhase",
]
