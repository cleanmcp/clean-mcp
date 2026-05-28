"""Tests for staleness detection."""

from __future__ import annotations

import subprocess
import time
from unittest.mock import MagicMock, patch

import pytest

from clean.core.models import FileState, ProjectState
from clean.indexing.staleness import (
    _check_git_staleness,
    _check_mtime_staleness,
    _project_id,
    check_staleness,
)


@pytest.fixture
def mock_store():
    return MagicMock()


@pytest.fixture
def make_state():
    """Factory for ProjectState with optional files and git_head."""

    def _make(
        project_id: str = "myproject",
        root_path: str = "/tmp/myproject",
        files: dict[str, FileState] | None = None,
        git_head: str | None = "abc123",
    ) -> ProjectState:
        return ProjectState(
            project_id=project_id,
            root_path=root_path,
            files=files or {},
            git_head=git_head,
        )

    return _make


# --- check_staleness (top-level) ---


def test_no_project_state_returns_stale(mock_store):
    """No prior index → stale."""
    mock_store.get_project_state.return_value = None
    assert check_staleness("/tmp/myproject", mock_store) is True


# --- git staleness ---


def test_git_head_changed():
    """HEAD moved since index → stale."""
    run_result = MagicMock()
    run_result.returncode = 0
    run_result.stdout = "def456\n"

    with patch(
        "clean.indexing.staleness.subprocess.run", return_value=run_result
    ) as mock_run:
        result = _check_git_staleness("/tmp/repo", "abc123")

    assert result is True
    # Should short-circuit: only rev-parse called, not git status
    assert mock_run.call_count == 1


def test_git_head_same_and_clean():
    """HEAD matches + clean working tree → not stale."""
    rev_parse_result = MagicMock()
    rev_parse_result.returncode = 0
    rev_parse_result.stdout = "abc123\n"

    status_result = MagicMock()
    status_result.returncode = 0
    status_result.stdout = ""

    with patch(
        "clean.indexing.staleness.subprocess.run",
        side_effect=[rev_parse_result, status_result],
    ):
        result = _check_git_staleness("/tmp/repo", "abc123")

    assert result is False


def test_git_head_same_but_uncommitted():
    """HEAD matches + dirty working tree → stale."""
    rev_parse_result = MagicMock()
    rev_parse_result.returncode = 0
    rev_parse_result.stdout = "abc123\n"

    status_result = MagicMock()
    status_result.returncode = 0
    status_result.stdout = " M src/main.py\n"

    with patch(
        "clean.indexing.staleness.subprocess.run",
        side_effect=[rev_parse_result, status_result],
    ):
        result = _check_git_staleness("/tmp/repo", "abc123")

    assert result is True


def test_stored_head_none_still_checks_status():
    """No stored head → skips HEAD comparison, still runs git status."""
    rev_parse_result = MagicMock()
    rev_parse_result.returncode = 0
    rev_parse_result.stdout = "abc123\n"

    status_result = MagicMock()
    status_result.returncode = 0
    status_result.stdout = "?? new_file.py\n"

    with patch(
        "clean.indexing.staleness.subprocess.run",
        side_effect=[rev_parse_result, status_result],
    ):
        result = _check_git_staleness("/tmp/repo", None)

    assert result is True


def test_git_nonzero_return_falls_back():
    """git rev-parse fails (not a repo) → returns None (fall back to mtime)."""
    run_result = MagicMock()
    run_result.returncode = 128

    with patch("clean.indexing.staleness.subprocess.run", return_value=run_result):
        result = _check_git_staleness("/tmp/repo", "abc123")

    assert result is None


def test_git_timeout_falls_back_to_mtime():
    """Git times out → returns None (fall back to mtime)."""
    with patch(
        "clean.indexing.staleness.subprocess.run",
        side_effect=subprocess.TimeoutExpired("git", 5),
    ):
        result = _check_git_staleness("/tmp/repo", "abc123")

    assert result is None


# --- mtime staleness ---


def test_no_git_mtime_unchanged(tmp_path, make_state):
    """No git, files not modified → not stale."""
    f = tmp_path / "hello.py"
    f.write_text("def hello(): pass")
    # Set last_indexed_at to the future so mtime < last_indexed_at
    future_time = time.time() + 1000
    state = make_state(
        files={str(f): FileState(str(f), "hash", 1, future_time)},
    )
    assert _check_mtime_staleness(state) is False


def test_no_git_mtime_changed(tmp_path, make_state):
    """No git, file mtime newer → stale."""
    f = tmp_path / "hello.py"
    f.write_text("def hello(): pass")
    # Set last_indexed_at in the past
    past_time = time.time() - 1000
    state = make_state(
        files={str(f): FileState(str(f), "hash", 1, past_time)},
    )
    assert _check_mtime_staleness(state) is True


def test_no_git_file_deleted(make_state):
    """No git, tracked file missing → stale."""
    state = make_state(
        files={
            "/nonexistent/file.py": FileState(
                "/nonexistent/file.py", "hash", 1, time.time()
            )
        },
    )
    assert _check_mtime_staleness(state) is True


def test_mtime_empty_file_states(make_state):
    """No tracked files → not stale."""
    state = make_state(files={})
    assert _check_mtime_staleness(state) is False


def test_git_timeout_falls_back_to_mtime_integration(tmp_path, mock_store, make_state):
    """Git times out → falls back to mtime check which detects staleness."""
    f = tmp_path / "hello.py"
    f.write_text("def hello(): pass")
    past_time = time.time() - 1000

    state = make_state(
        project_id=_project_id(str(tmp_path)),
        files={str(f): FileState(str(f), "hash", 1, past_time)},
        git_head="abc123",
    )
    mock_store.get_project_state.return_value = state

    with patch(
        "clean.indexing.staleness.subprocess.run",
        side_effect=subprocess.TimeoutExpired("git", 5),
    ):
        assert check_staleness(str(tmp_path), mock_store) is True


# --- project_id ---


def test_project_id_basic():
    """Path → project_id conversion."""
    assert _project_id("/home/user/MyProject") == "myproject"


def test_project_id_with_spaces():
    """Spaces replaced with underscores."""
    assert _project_id("/home/user/My Project") == "my_project"
