"""Tests for MetadataStore (real SQLite, temp file)."""

from __future__ import annotations

import os

import pytest

from clean.db.metadata import MetadataStore
from clean.db.models import InstallationRecord, ProjectRecord


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "test.db")
    return MetadataStore(db_path)


@pytest.fixture
def installation():
    return InstallationRecord(
        installation_id=12345,
        account_login="octocat",
        account_type="User",
        created_at="2024-01-01T00:00:00Z",
    )


@pytest.fixture
def project():
    return ProjectRecord(
        project_id="octocat_hello",
        repo_full_name="octocat/hello",
        installation_id=12345,
        local_path="/tmp/repos/octocat/hello",
        status="cloning",
        created_at="2024-01-01T00:00:00Z",
    )


class TestMetadataStore:
    def test_save_and_get_installation(self, store, installation):
        store.save_installation(installation)
        result = store.get_installation(12345)
        assert result is not None
        assert result.installation_id == 12345
        assert result.account_login == "octocat"
        assert result.account_type == "User"

    def test_get_installation_not_found(self, store):
        assert store.get_installation(99999) is None

    def test_delete_installation(self, store, installation):
        store.save_installation(installation)
        store.delete_installation(12345)
        assert store.get_installation(12345) is None

    def test_save_installation_upsert(self, store, installation):
        store.save_installation(installation)
        updated = InstallationRecord(
            installation_id=12345,
            account_login="octocat-updated",
            account_type="Organization",
            created_at="2024-06-01T00:00:00Z",
        )
        store.save_installation(updated)
        result = store.get_installation(12345)
        assert result.account_login == "octocat-updated"

    def test_save_and_get_project(self, store, installation, project):
        store.save_installation(installation)
        store.save_project(project)
        result = store.get_project("octocat_hello")
        assert result is not None
        assert result.repo_full_name == "octocat/hello"
        assert result.status == "cloning"

    def test_get_project_not_found(self, store):
        assert store.get_project("nonexistent") is None

    def test_get_project_by_repo(self, store, installation, project):
        store.save_installation(installation)
        store.save_project(project)
        result = store.get_project_by_repo("octocat/hello")
        assert result is not None
        assert result.project_id == "octocat_hello"

    def test_get_project_by_repo_not_found(self, store):
        assert store.get_project_by_repo("nonexistent/repo") is None

    def test_update_project_status(self, store, installation, project):
        store.save_installation(installation)
        store.save_project(project)
        store.update_project_status("octocat_hello", "ready", entity_count=42)
        result = store.get_project("octocat_hello")
        assert result.status == "ready"
        assert result.entity_count == 42
        assert result.last_indexed_at is not None

    def test_update_project_status_error(self, store, installation, project):
        store.save_installation(installation)
        store.save_project(project)
        store.update_project_status("octocat_hello", "error", error_message="boom")
        result = store.get_project("octocat_hello")
        assert result.status == "error"
        assert result.error_message == "boom"

    def test_list_projects_all(self, store, installation, project):
        store.save_installation(installation)
        store.save_project(project)
        projects = store.list_projects()
        assert len(projects) == 1

    def test_list_projects_by_installation(self, store, installation, project):
        store.save_installation(installation)
        store.save_project(project)
        projects = store.list_projects(installation_id=12345)
        assert len(projects) == 1
        projects = store.list_projects(installation_id=99999)
        assert len(projects) == 0

    def test_delete_project(self, store, installation, project):
        store.save_installation(installation)
        store.save_project(project)
        store.delete_project("octocat_hello")
        assert store.get_project("octocat_hello") is None

    def test_save_project_upsert(self, store, installation, project):
        store.save_installation(installation)
        store.save_project(project)
        updated = ProjectRecord(
            project_id="octocat_hello",
            repo_full_name="octocat/hello",
            installation_id=12345,
            local_path="/tmp/repos/octocat/hello",
            status="ready",
            entity_count=100,
            created_at="2024-01-01T00:00:00Z",
        )
        store.save_project(updated)
        result = store.get_project("octocat_hello")
        assert result.status == "ready"
        assert result.entity_count == 100

    def test_creates_parent_dirs(self, tmp_path):
        db_path = str(tmp_path / "nested" / "dir" / "test.db")
        MetadataStore(db_path)
        assert os.path.exists(os.path.dirname(db_path))

