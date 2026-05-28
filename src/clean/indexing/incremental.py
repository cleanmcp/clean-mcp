"""Git-aware incremental indexing with change detection."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field

from ..core.models import ProjectState
from ..core.protocols import VectorStore
from ..util.hashing import hash_file
from ..util.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ChangeSet:
    """Files that changed since last index."""

    added: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)


class IncrementalIndexer:
    """Detect changed files using git diff or content hashing."""

    def __init__(self, store: VectorStore) -> None:
        self._store = store

    def detect_changes(
        self,
        project_id: str,
        root_path: str,
        current_files: list[str],
    ) -> ChangeSet:
        """
        Compare current files against stored project state.

        Uses git diff for speed if available, falls back to SHA-256 hashing.
        """
        state = self._store.get_project_state(project_id)

        if state is None:
            # First index — everything is new
            return ChangeSet(added=current_files)

        # Try git-based detection first
        git_changes = self._try_git_diff(root_path, state.git_head)
        if git_changes is not None:
            return self._apply_git_changes(git_changes, current_files, state)

        # Fallback: content hash comparison
        return self._hash_based_diff(current_files, state)

    def _try_git_diff(self, root_path: str, stored_head: str | None) -> set[str] | None:
        """Use git to detect changed files. Returns None if git unavailable."""
        if stored_head is None:
            return None

        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", f"{stored_head}..HEAD"],
                cwd=root_path,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                return None

            changed = set()
            for line in result.stdout.strip().split("\n"):
                if line:
                    changed.add(os.path.join(root_path, line))
            return changed

        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None

    def _apply_git_changes(
        self,
        git_changed: set[str],
        current_files: list[str],
        state: ProjectState,
    ) -> ChangeSet:
        changes = ChangeSet()
        current_set = set(current_files)
        stored_set = set(state.files.keys())

        for f in current_files:
            if f not in stored_set:
                changes.added.append(f)
            elif f in git_changed:
                changes.modified.append(f)
            else:
                changes.unchanged.append(f)

        for f in stored_set:
            if f not in current_set:
                changes.deleted.append(f)

        return changes

    def _hash_based_diff(
        self, current_files: list[str], state: ProjectState
    ) -> ChangeSet:
        """Compare file content hashes to detect changes."""
        changes = ChangeSet()
        current_set = set(current_files)
        stored_set = set(state.files.keys())

        for f in current_files:
            if f not in stored_set:
                changes.added.append(f)
            else:
                current_hash = hash_file(f)
                stored_hash = state.files[f].content_hash
                if current_hash != stored_hash:
                    changes.modified.append(f)
                else:
                    changes.unchanged.append(f)

        for f in stored_set:
            if f not in current_set:
                changes.deleted.append(f)

        return changes

    @staticmethod
    def get_git_head(root_path: str) -> str | None:
        """Get the current git HEAD hash."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return None
