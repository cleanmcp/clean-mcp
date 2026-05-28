"""SHA-256 file content hashing."""

from __future__ import annotations

import hashlib


def hash_file_content(content: bytes) -> str:
    """Return the SHA-256 hex digest of file content."""
    return hashlib.sha256(content).hexdigest()


def hash_file(path: str) -> str:
    """Read a file and return its SHA-256 hex digest."""
    with open(path, "rb") as f:
        return hash_file_content(f.read())
