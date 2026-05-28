"""Exception hierarchy for Clean."""

from __future__ import annotations


class CleanError(Exception):
    """Base exception for all Clean errors."""


class SecurityError(CleanError):
    """Base exception for security-related errors."""


class PathTraversalError(SecurityError):
    """Raised when path traversal is detected."""


class SymlinkError(SecurityError):
    """Raised when unsafe symlink is detected."""


class FileSizeError(SecurityError):
    """Raised when file exceeds size limit."""


class InputValidationError(SecurityError):
    """Raised when input validation fails."""


class EmbeddingValidationError(SecurityError):
    """Raised when embedding validation fails."""


class IndexingError(CleanError):
    """Raised when indexing fails."""


class SearchError(CleanError):
    """Raised when search fails."""


class StorageError(CleanError):
    """Raised when storage operations fail."""


class ParsingError(CleanError):
    """Raised when parsing fails."""


class GitHubError(CleanError):
    """Raised when GitHub API operations fail."""


class WebhookValidationError(SecurityError):
    """Raised when webhook signature validation fails."""


class RepoError(CleanError):
    """Raised when repository operations fail."""


class JobError(CleanError):
    """Raised when background job operations fail."""


class LicenseError(CleanError):
    """Raised when license validation fails."""
