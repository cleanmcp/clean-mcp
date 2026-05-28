"""Staleness detection for auto-reindexing on MCP search."""

from __future__ import annotations

import os
import subprocess

from ..core.models import ProjectState
from ..core.protocols import VectorStore
from ..util.logging import get_logger

logger = get_logger(__name__)

_GIT_TIMEOUT = 5


def _project_id(path: str) -> str:
    """Generate a project ID from path (mirrors CodebaseIndexer._project_id)."""
    return os.path.basename(path).lower().replace(" ", "_")


def _check_git_staleness(project_path: str, stored_head: str | None) -> bool | None:
    """Check staleness via git. Returns True/False, or None if git unavailable."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        if result.returncode != 0:
            return None  # Not a git repo

        current_head = result.stdout.strip()

        if stored_head is not None and current_head != stored_head:
            return True

        # Check for uncommitted/staged/untracked changes
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        if status_result.returncode != 0:
            return None

        if status_result.stdout.strip():
            return True

        return False

    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _check_mtime_staleness(state: ProjectState) -> bool:
    """Check staleness via file mtime. Returns True if any tracked file changed."""
    for file_path, file_state in state.files.items():
        try:
            mtime = os.stat(file_path).st_mtime
            if mtime > file_state.last_indexed_at:
                return True
        except OSError:
            # File deleted
            return True
    return False


def check_staleness(
    project_path: str, store: VectorStore, project_id: str | None = None
) -> bool:
    """Check if a project's index is stale and needs re-indexing.

    Uses git (HEAD comparison + working tree status) when available,
    falls back to mtime comparison on tracked files.

    Args:
        project_path: Path to the project on disk.
        store: Vector store to check state against.
        project_id: Explicit project ID. Falls back to _project_id(path) if None.

    Returns True if stale, False if up-to-date.
    """
    if project_id is None:
        project_id = _project_id(project_path)
    state = store.get_project_state(project_id)

    if state is None:
        return True

    git_result = _check_git_staleness(project_path, state.git_head)
    if git_result is not None:
        return git_result

    return _check_mtime_staleness(state)
