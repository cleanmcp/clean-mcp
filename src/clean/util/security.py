"""Path validation, input sanitization, and security checks."""

from __future__ import annotations

import os
import re

from ..core.errors import (
    InputValidationError,
    PathTraversalError,
    FileSizeError,
    EmbeddingValidationError,
)

MAX_FILE_SIZE_MB = 10
MAX_QUERY_LENGTH = 1000

SENSITIVE_FILE_PATTERNS = [
    r"\.env$",
    r"\.env\.(?!example$|template$|sample$)",
    r"credentials",
    r"secrets?\.",
    r"private[_-]?key",
    r"\.pem$",
    r"\.key$",
    r"\.p12$",
    r"\.pfx$",
    r"id_rsa",
    r"id_dsa",
    r"id_ecdsa",
    r"id_ed25519",
]

DANGEROUS_PATTERNS = [
    r"\.\./",
    r"\.\.\\",
    r"\x00",
]


def sanitize_path(path: str) -> str:
    """Normalize and clean a file path."""
    if not path:
        raise InputValidationError("Path cannot be empty")
    if not isinstance(path, str):
        raise InputValidationError(f"Path must be a string, got {type(path).__name__}")
    if "\x00" in path:
        raise InputValidationError("Path contains null bytes")
    return os.path.normpath(path)


def validate_path(path: str, base_dir: str) -> str:
    """Validate that a path stays within base_dir. Returns resolved path."""
    if not path:
        raise InputValidationError("Path cannot be empty")
    if not base_dir:
        raise InputValidationError("Base directory cannot be empty")

    path = sanitize_path(path)
    base_dir = sanitize_path(base_dir)

    abs_base = os.path.abspath(base_dir)
    abs_path = (
        os.path.abspath(os.path.join(abs_base, path))
        if not os.path.isabs(path)
        else os.path.abspath(path)
    )

    try:
        real_path = os.path.realpath(abs_path)
        real_base = os.path.realpath(abs_base)
    except (OSError, ValueError) as e:
        raise PathTraversalError(f"Cannot resolve path: {e}")

    if not real_path.startswith(real_base + os.sep) and real_path != real_base:
        raise PathTraversalError(
            f"Path '{path}' resolves to '{real_path}' which is outside '{real_base}'"
        )
    return real_path


def validate_file_size(path: str, max_size_mb: float = MAX_FILE_SIZE_MB) -> bool:
    """Check file is within size limit."""
    if not os.path.exists(path):
        raise InputValidationError(f"File does not exist: {path}")
    if not os.path.isfile(path):
        raise InputValidationError(f"Path is not a file: {path}")
    file_size = os.path.getsize(path)
    max_bytes = max_size_mb * 1024 * 1024
    if file_size > max_bytes:
        raise FileSizeError(
            f"File '{path}' is {file_size / (1024 * 1024):.2f}MB, exceeds limit of {max_size_mb}MB"
        )
    return True


def is_safe_to_index(
    path: str,
    base_dir: str,
    max_size_mb: float = MAX_FILE_SIZE_MB,
) -> tuple[bool, str | None]:
    """Combined safety check. Returns (is_safe, reason)."""
    try:
        validated = validate_path(path, base_dir)
        if not os.path.isfile(validated):
            return False, f"Not a file: {path}"
        if os.path.islink(path):
            real = os.path.realpath(path)
            real_base = os.path.realpath(base_dir)
            if not real.startswith(real_base + os.sep) and real != real_base:
                return False, f"Symlink points outside base directory: {path}"
        validate_file_size(validated, max_size_mb)
        filename = os.path.basename(validated).lower()
        for pattern in SENSITIVE_FILE_PATTERNS:
            if re.search(pattern, filename, re.IGNORECASE):
                return False, f"Sensitive file detected: {path}"
        return True, None
    except Exception as e:
        return False, str(e)


def validate_query(query: str, max_length: int = MAX_QUERY_LENGTH) -> str:
    """Validate and sanitize a search query."""
    if query is None:
        raise InputValidationError("Query cannot be None")
    if not isinstance(query, str):
        raise InputValidationError(
            f"Query must be a string, got {type(query).__name__}"
        )
    query = query.strip()
    if not query:
        raise InputValidationError("Query cannot be empty")
    if len(query) > max_length:
        raise InputValidationError(f"Query exceeds maximum length of {max_length}")
    if "\x00" in query:
        raise InputValidationError("Query contains null bytes")
    return query


def validate_embedding(embedding: list, expected_dim: int) -> bool:
    """Validate embedding dimensions and values."""
    if embedding is None:
        raise EmbeddingValidationError("Embedding cannot be None")
    if not isinstance(embedding, (list, tuple)):
        raise EmbeddingValidationError(
            f"Embedding must be a list, got {type(embedding).__name__}"
        )
    if len(embedding) != expected_dim:
        raise EmbeddingValidationError(
            f"Embedding has {len(embedding)} dimensions, expected {expected_dim}"
        )
    for i, val in enumerate(embedding):
        if not isinstance(val, (int, float)):
            raise EmbeddingValidationError(f"Non-numeric at index {i}")
        if isinstance(val, float) and (val != val or abs(val) == float("inf")):
            raise EmbeddingValidationError(f"NaN or Inf at index {i}")
    return True


def validate_persist_path(persist_path: str) -> str:
    """Validate a database persistence path."""
    if not persist_path:
        raise InputValidationError("Persist path cannot be empty")
    cleaned = sanitize_path(persist_path)
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, persist_path):
            raise PathTraversalError("Persist path contains dangerous pattern")
    abs_path = os.path.abspath(cleaned)
    parent = os.path.dirname(abs_path)
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)
    return abs_path
