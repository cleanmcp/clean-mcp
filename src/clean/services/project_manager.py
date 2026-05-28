"""Multi-project registry."""

from __future__ import annotations

import os

from ..core.protocols import VectorStore


class ProjectManager:
    """Track and manage multiple indexed projects."""

    def __init__(self, store: VectorStore) -> None:
        self._store = store
        self._projects: dict[str, str] = {}  # project_id -> root_path

    def register(self, project_path: str) -> str:
        """Register a project and return its ID."""
        abs_path = os.path.abspath(project_path)
        project_id = os.path.basename(abs_path).lower().replace(" ", "_")
        self._projects[project_id] = abs_path
        return project_id

    def get_path(self, project_id: str) -> str | None:
        return self._projects.get(project_id)

    def list_projects(self) -> dict[str, str]:
        return dict(self._projects)
