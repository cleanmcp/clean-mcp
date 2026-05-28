"""Directory walking with gitignore and skip pattern support."""

from __future__ import annotations

import os

from ..core.config import CleanConfig
from ..util.logging import get_logger
from ..util.security import is_safe_to_index, MAX_FILE_SIZE_MB

logger = get_logger(__name__)


class FileScanner:
    """Walk a directory tree and yield files safe to index."""

    def __init__(self, config: CleanConfig) -> None:
        self._skip_dirs = config.indexer.skip_dirs
        self._supported_extensions: set[str] = set(
            config.parser.extension_languages.keys()
        )

    def scan(
        self, root_path: str, max_file_size_mb: float = MAX_FILE_SIZE_MB
    ) -> list[str]:
        """
        Return all indexable file paths under root_path.

        Skips configured directories, symlinked directories,
        unsupported extensions, and sensitive files.
        """
        abs_root = os.path.abspath(root_path)
        files: list[str] = []

        for dirpath, dirnames, filenames in os.walk(abs_root, followlinks=False):
            # Filter out skip dirs and symlinked dirs
            dirnames[:] = [
                d
                for d in dirnames
                if d not in self._skip_dirs
                and not os.path.islink(os.path.join(dirpath, d))
            ]

            for filename in filenames:
                _, ext = os.path.splitext(filename)
                if ext not in self._supported_extensions:
                    continue

                filepath = os.path.join(dirpath, filename)
                safe, reason = is_safe_to_index(filepath, abs_root, max_file_size_mb)
                if not safe:
                    logger.debug("Skipping %s: %s", filepath, reason)
                    continue

                files.append(filepath)

        return files
