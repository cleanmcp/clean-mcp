"""SQLite metadata store for installations and projects."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .models import InstallationRecord, ProjectRecord


class MetadataStore:
    """SQLite-backed store for GitHub installations and indexed projects."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_tables()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self._db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_tables(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS installations (
                    installation_id INTEGER PRIMARY KEY,
                    account_login TEXT NOT NULL,
                    account_type TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    repo_full_name TEXT NOT NULL UNIQUE,
                    installation_id INTEGER,
                    local_path TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'cloning',
                    entity_count INTEGER NOT NULL DEFAULT 0,
                    last_indexed_at TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    org_id TEXT,
                    FOREIGN KEY (installation_id) REFERENCES installations(installation_id) ON DELETE SET NULL
                )
            """)
            # Migration: add org_id column if missing (existing DBs)
            try:
                conn.execute("ALTER TABLE projects ADD COLUMN org_id TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists
            # Migration: add metadata columns if missing (existing DBs)
            for col_def in (
                "description TEXT",
                "primary_language TEXT",
                "tags TEXT",
            ):
                try:
                    conn.execute(f"ALTER TABLE projects ADD COLUMN {col_def}")
                except sqlite3.OperationalError:
                    pass  # Column already exists
            # Migration: add branch support — recreate table to drop UNIQUE on
            # repo_full_name (same repo can now be indexed on multiple branches).
            # The entire migration runs inside a savepoint so a crash between
            # DROP TABLE and RENAME cannot orphan data.
            cols = conn.execute("PRAGMA table_info(projects)").fetchall()
            col_names = [c[1] for c in cols]
            if "branch" not in col_names:
                # Drop the migration staging table if it was left behind by a
                # previous crash so the CREATE below cannot fail.
                conn.execute("DROP TABLE IF EXISTS projects_branch_migration")
                conn.execute("""
                    CREATE TABLE projects_branch_migration (
                        project_id TEXT PRIMARY KEY,
                        repo_full_name TEXT NOT NULL,
                        branch TEXT,
                        installation_id INTEGER,
                        local_path TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'cloning',
                        entity_count INTEGER NOT NULL DEFAULT 0,
                        last_indexed_at TEXT,
                        error_message TEXT,
                        created_at TEXT NOT NULL,
                        org_id TEXT,
                        description TEXT,
                        primary_language TEXT,
                        tags TEXT,
                        FOREIGN KEY (installation_id) REFERENCES installations(installation_id) ON DELETE SET NULL
                    )
                """)
                conn.execute("""
                    INSERT INTO projects_branch_migration
                    (project_id, repo_full_name, branch, installation_id, local_path,
                     status, entity_count, last_indexed_at, error_message, created_at,
                     org_id, description, primary_language, tags)
                    SELECT project_id, repo_full_name, NULL, installation_id, local_path,
                           status, entity_count, last_indexed_at, error_message, created_at,
                           org_id, description, primary_language, tags
                    FROM projects
                """)
                conn.execute("DROP TABLE projects")
                conn.execute("ALTER TABLE projects_branch_migration RENAME TO projects")
                conn.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_repo_branch
                    ON projects (repo_full_name, COALESCE(branch, ''))
                """)

    def save_installation(self, record: InstallationRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO installations
                   (installation_id, account_login, account_type, created_at)
                   VALUES (?, ?, ?, ?)""",
                (
                    record.installation_id,
                    record.account_login,
                    record.account_type,
                    record.created_at,
                ),
            )

    def get_installation(self, installation_id: int) -> InstallationRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM installations WHERE installation_id = ?",
                (installation_id,),
            ).fetchone()
        if row is None:
            return None
        return InstallationRecord(**dict(row))

    def delete_installation(self, installation_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM installations WHERE installation_id = ?",
                (installation_id,),
            )

    def save_project(self, record: ProjectRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO projects
                   (project_id, repo_full_name, branch, installation_id, local_path,
                    status, entity_count, last_indexed_at, error_message, created_at, org_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.project_id,
                    record.repo_full_name,
                    record.branch,
                    record.installation_id,
                    record.local_path,
                    record.status,
                    record.entity_count,
                    record.last_indexed_at,
                    record.error_message,
                    record.created_at,
                    record.org_id,
                ),
            )

    def get_project(self, project_id: str) -> ProjectRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        if row is None:
            return None
        return ProjectRecord(**dict(row))

    def get_project_by_repo(
        self, repo_full_name: str, branch: str | None = None
    ) -> ProjectRecord | None:
        with self._connect() as conn:
            if branch is None:
                row = conn.execute(
                    "SELECT * FROM projects WHERE repo_full_name = ? AND branch IS NULL",
                    (repo_full_name,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM projects WHERE repo_full_name = ? AND branch = ?",
                    (repo_full_name, branch),
                ).fetchone()
        if row is None:
            return None
        return ProjectRecord(**dict(row))

    def resolve_project(
        self, repo: str, org_id: str | None = None, branch: str | None = None
    ) -> ProjectRecord | None:
        """Fuzzy-resolve a repo name to a project.

        Handles:
        - Exact match: "lokus-ai/lokus"
        - Bare name: "lokus" → match by repo_full_name suffix
        - Owner mismatch: "pratham/lokus" → "lokus-ai/lokus"

        If branch is provided, only projects on that branch are matched.
        If org_id is provided, only repos belonging to that org (or unscoped) are returned.
        """
        # 1. Exact match (respects branch)
        project = self.get_project_by_repo(repo, branch)
        if project:
            if org_id is None or getattr(project, "org_id", None) in (None, org_id):
                return project

        # 2. Fuzzy match by repo name
        parts = repo.split("/")
        repo_name = parts[-1] if parts else repo
        matches = self.find_projects_by_repo_name(repo_name)

        # Filter by branch if specified
        if branch is not None:
            matches = [m for m in matches if m.branch == branch]

        # Filter by org
        if org_id is not None:
            matches = [
                m for m in matches if getattr(m, "org_id", None) in (None, org_id)
            ]

        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            # Prefer ready ones
            ready = [m for m in matches if m.status == "ready"]
            return ready[0] if ready else matches[0]

        return None

    def update_project_status(
        self,
        project_id: str,
        status: str,
        entity_count: int | None = None,
        error_message: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            if entity_count is not None:
                conn.execute(
                    """UPDATE projects
                       SET status = ?, entity_count = ?, last_indexed_at = ?, error_message = ?
                       WHERE project_id = ?""",
                    (status, entity_count, now, error_message, project_id),
                )
            else:
                conn.execute(
                    """UPDATE projects SET status = ?, error_message = ?
                       WHERE project_id = ?""",
                    (status, error_message, project_id),
                )

    def update_project_org(self, project_id: str, org_id: str) -> None:
        """Adopt a project into an org (set org_id)."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE projects SET org_id = ? WHERE project_id = ?",
                (org_id, project_id),
            )

    def update_project_local_path(self, project_id: str, local_path: str) -> None:
        """Update the local_path for a project (e.g. after clone resolves the actual path)."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE projects SET local_path = ? WHERE project_id = ?",
                (local_path, project_id),
            )

    def update_project_metadata(
        self,
        project_id: str,
        description: str | None = None,
        primary_language: str | None = None,
        tags: str | None = None,
    ) -> None:
        """Update auto-detected metadata fields for a project.

        Only sets fields that are explicitly passed (non-None values overwrite;
        passing None leaves the existing DB value unchanged).
        """
        fields: list[str] = []
        values: list = []
        if description is not None:
            fields.append("description = ?")
            values.append(description)
        if primary_language is not None:
            fields.append("primary_language = ?")
            values.append(primary_language)
        if tags is not None:
            fields.append("tags = ?")
            values.append(tags)
        if not fields:
            return
        values.append(project_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE projects SET {', '.join(fields)} WHERE project_id = ?",
                values,
            )

    def list_projects(
        self,
        installation_id: int | None = None,
        org_id: str | None = None,
    ) -> list[ProjectRecord]:
        with self._connect() as conn:
            if installation_id is not None:
                rows = conn.execute(
                    "SELECT * FROM projects WHERE installation_id = ?",
                    (installation_id,),
                ).fetchall()
            elif org_id is not None:
                rows = conn.execute(
                    "SELECT * FROM projects WHERE org_id = ?",
                    (org_id,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM projects").fetchall()
        return [ProjectRecord(**dict(r)) for r in rows]

    def find_projects_by_repo_name(self, repo_name: str) -> list[ProjectRecord]:
        """Find projects whose repo_full_name ends with /<repo_name>."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM projects WHERE repo_full_name LIKE ?",
                (f"%/{repo_name}",),
            ).fetchall()
        return [ProjectRecord(**dict(r)) for r in rows]

    def count_ready_projects(self) -> int:
        """Count projects with status='ready'."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM projects WHERE status = 'ready'"
            ).fetchone()
        return row[0] if row else 0

    def delete_project(self, project_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM projects WHERE project_id = ?", (project_id,))
