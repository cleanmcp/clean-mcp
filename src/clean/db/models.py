"""Database record dataclasses."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InstallationRecord:
    installation_id: int
    account_login: str
    account_type: str  # "User" or "Organization"
    created_at: str


@dataclass(frozen=True)
class ProjectRecord:
    project_id: str
    repo_full_name: str
    local_path: str
    status: str  # "cloning", "indexing", "ready", "error"
    installation_id: int | None = None  # None for MCP-initiated indexing
    entity_count: int = 0
    last_indexed_at: str | None = None
    error_message: str | None = None
    created_at: str = ""
    org_id: str | None = None
    description: str | None = None
    primary_language: str | None = None
    tags: str | None = None  # JSON array string, e.g. '["Python", "FastAPI"]'
    branch: str | None = None  # None means default branch
    base_branch: str | None = None  # Base branch for comparison
