"""Tests for RepoManager (mock subprocess)."""

from __future__ import annotations

import os
import subprocess

import pytest

from clean.core.errors import RepoError
from clean.repo.manager import RepoManager


@pytest.fixture
def repo_manager(tmp_path):
    return RepoManager(str(tmp_path / "repos"))


class TestRepoManager:
    def test_repo_path(self, repo_manager):
        path = repo_manager.repo_path("octocat/hello-world")
        assert path.endswith(os.path.join("repos", "octocat", "hello-world"))

    def test_exists_false(self, repo_manager):
        assert repo_manager.exists("octocat/hello-world") is False

    def test_exists_true(self, repo_manager, tmp_path):
        dest = os.path.join(str(tmp_path / "repos"), "octocat", "hello-world", ".git")
        os.makedirs(dest)
        assert repo_manager.exists("octocat/hello-world") is True

    def test_clone_success(self, repo_manager, mocker):
        mocker.patch("subprocess.run")
        path = repo_manager.clone(
            "https://github.com/octocat/hello.git", "octocat/hello"
        )
        assert path.endswith(os.path.join("octocat", "hello"))
        subprocess.run.assert_called_once()
        call_args = subprocess.run.call_args
        assert call_args[0][0][0] == "git"
        assert "--filter=blob:none" in call_args[0][0]
        assert "--single-branch" in call_args[0][0]

    def test_clone_failure(self, repo_manager, mocker):
        mocker.patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "git", stderr=b"fatal: error"),
        )
        with pytest.raises(RepoError, match="Clone failed"):
            repo_manager.clone("https://github.com/x/y.git", "x/y")

    def test_clone_timeout(self, repo_manager, mocker):
        mocker.patch(
            "subprocess.run", side_effect=subprocess.TimeoutExpired("git", 120)
        )
        with pytest.raises(RepoError, match="timed out"):
            repo_manager.clone("https://github.com/x/y.git", "x/y")

    def test_clone_existing_calls_pull(self, repo_manager, mocker, tmp_path):
        # Create .git dir to simulate existing clone
        dest = os.path.join(str(tmp_path / "repos"), "octocat", "hello", ".git")
        os.makedirs(dest)
        mocker.patch("subprocess.run")
        repo_manager.clone("https://github.com/octocat/hello.git", "octocat/hello")
        # Should have called pull (git pull --ff-only), not clone
        call_args = subprocess.run.call_args[0][0]
        assert "pull" in call_args

    def test_pull_not_cloned(self, repo_manager):
        with pytest.raises(RepoError, match="not cloned"):
            repo_manager.pull("nonexistent/repo")

    def test_pull_success(self, repo_manager, mocker, tmp_path):
        dest = os.path.join(str(tmp_path / "repos"), "octocat", "hello", ".git")
        os.makedirs(dest)
        mocker.patch("subprocess.run")
        repo_manager.pull("octocat/hello")
        call_args = subprocess.run.call_args[0][0]
        assert "pull" in call_args
        assert "--ff-only" in call_args

    def test_pull_failure(self, repo_manager, mocker, tmp_path):
        dest = os.path.join(str(tmp_path / "repos"), "octocat", "hello", ".git")
        os.makedirs(dest)
        mocker.patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "git", stderr=b"error"),
        )
        with pytest.raises(RepoError, match="Pull failed"):
            repo_manager.pull("octocat/hello")

    def test_delete(self, repo_manager, tmp_path):
        dest = os.path.join(str(tmp_path / "repos"), "octocat", "hello")
        os.makedirs(dest)
        repo_manager.delete("octocat/hello")
        assert not os.path.exists(dest)

    def test_delete_nonexistent(self, repo_manager):
        # Should not raise
        repo_manager.delete("nonexistent/repo")

    def test_update_remote_url(self, repo_manager, mocker, tmp_path):
        dest = os.path.join(str(tmp_path / "repos"), "octocat", "hello", ".git")
        os.makedirs(dest)
        mocker.patch("subprocess.run")
        repo_manager.update_remote_url("octocat/hello", "https://new-url.git")
        call_args = subprocess.run.call_args[0][0]
        assert "set-url" in call_args

    def test_update_remote_url_not_cloned(self, repo_manager):
        with pytest.raises(RepoError, match="not cloned"):
            repo_manager.update_remote_url("nonexistent/repo", "https://x.git")
