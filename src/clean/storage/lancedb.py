"""LanceDB vector store implementation."""

from __future__ import annotations

import json
import os
import re
import threading
from typing import Sequence

import lancedb
import pyarrow as pa

from ..core.config import StorageConfig
from ..core.errors import StorageError
from ..core.models import CodeEntity, FileState, ProjectState, SearchResult
from ..core.types import EntityKind, Language
from ..util.logging import get_logger

logger = get_logger(__name__)

# Schema for the entities table
ENTITY_SCHEMA = pa.schema(
    [
        pa.field("id", pa.utf8()),
        pa.field("project_id", pa.utf8()),
        pa.field("name", pa.utf8()),
        pa.field("file_path", pa.utf8()),
        pa.field("code", pa.utf8()),
        pa.field("line_start", pa.int32()),
        pa.field("line_end", pa.int32()),
        pa.field("language", pa.utf8()),
        pa.field("kind", pa.utf8()),
        pa.field("calls", pa.utf8()),  # JSON-encoded list
        pa.field("called_by", pa.utf8()),  # JSON-encoded list
        pa.field("class_name", pa.utf8()),
        pa.field("exported", pa.bool_()),
        pa.field("sub_kind", pa.utf8()),  # nullable; empty string means None
        pa.field("decorators", pa.utf8()),  # JSON-encoded list, nullable
        pa.field("vector", pa.list_(pa.float32())),
        pa.field("chunk_index", pa.int32()),  # 0 for non-chunked
        pa.field("parent_id", pa.utf8()),  # "" when not a chunk
        pa.field("total_chunks", pa.int32()),  # 0 for non-chunked
    ]
)


def _escape_lance(value: str) -> str:
    """Escape a string value for LanceDB WHERE clauses."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _sanitize_like_pattern(pattern: str) -> str:
    """Sanitize a pattern for use inside a SQL LIKE clause.

    Keeps only alphanumeric characters, underscores, hyphens, dots, and
    forward-slashes — enough to match identifiers and file-path fragments
    while preventing SQL injection.  SQL wildcard characters (``%``, ``_``)
    that are part of the original pattern are stripped so they cannot be
    abused as arbitrary wildcards.

    Args:
        pattern: Raw user-supplied pattern string.

    Returns:
        A sanitised pattern safe for interpolation into a LIKE clause.
    """
    return re.sub(r"[^\w\-./]", "", pattern)


class LanceDBStore:
    """LanceDB-backed vector store. Embedded, no server needed."""

    def __init__(self, config: StorageConfig | None = None) -> None:
        self._config = config or StorageConfig()
        self._db: lancedb.DBConnection | None = None
        self._tables: dict[str, lancedb.table.Table] = {}
        self._lock = threading.RLock()

    def _get_db(self, project_id: str) -> lancedb.DBConnection:
        """Get or create a LanceDB connection for a project."""
        with self._lock:
            if self._db is None:
                db_path = self._config.default_persist_path
                os.makedirs(db_path, exist_ok=True)
                self._db = lancedb.connect(db_path)
            return self._db

    def _table_name(self, project_id: str) -> str:
        # Sanitize project_id for use as table name
        return f"entities_{project_id.replace('/', '_').replace('.', '_')}"

    def _state_table_name(self, project_id: str) -> str:
        return f"state_{project_id.replace('/', '_').replace('.', '_')}"

    def _list_tables(self, db) -> list[str]:
        """List table names, handling API differences across versions."""
        try:
            result = db.list_tables()
            # Newer API returns ListTablesResponse with .tables attribute
            if hasattr(result, "tables"):
                return list(result.tables)
            return list(result)
        except (AttributeError, TypeError):
            # Fallback to deprecated API
            return list(db.table_names())

    def _get_table(self, project_id: str) -> lancedb.table.Table | None:
        name = self._table_name(project_id)
        if name in self._tables:
            return self._tables[name]
        db = self._get_db(project_id)
        if name in self._list_tables(db):
            table = db.open_table(name)
            self._tables[name] = table
            return table
        return None

    def initialize(self, project_id: str, dimension: int) -> None:
        """Ensure the table exists for a project."""
        db = self._get_db(project_id)
        name = self._table_name(project_id)
        existing_tables = self._list_tables(db)
        if name not in existing_tables:
            # Create empty table with schema
            schema = ENTITY_SCHEMA.set(
                ENTITY_SCHEMA.get_field_index("vector"),
                pa.field("vector", pa.list_(pa.float32(), dimension)),
            )
            table = db.create_table(name, schema=schema)
            self._tables[name] = table
            logger.info("Created table '%s' with dimension %d", name, dimension)
        else:
            # Validate that existing table has the expected vector dimension
            table = db.open_table(name)
            self._tables[name] = table
            try:
                schema = table.schema
                vector_idx = schema.get_field_index("vector")
                vector_field = schema.field(vector_idx)
                list_size = vector_field.type.list_size
                if list_size is not None and list_size != dimension:
                    raise ValueError(
                        f"Table '{name}' has vector dimension {list_size}, "
                        f"expected {dimension}. Delete and re-index to fix."
                    )
            except (AttributeError, KeyError):
                # Schema introspection not available; skip validation
                pass

    def _entity_to_row(self, project_id: str, entity: CodeEntity) -> dict:
        return {
            "id": entity.id,
            "project_id": project_id,
            "name": entity.name,
            "file_path": entity.file_path,
            "code": entity.code,
            "line_start": entity.line_start,
            "line_end": entity.line_end,
            "language": entity.language.value
            if isinstance(entity.language, Language)
            else entity.language,
            "kind": entity.kind.value
            if isinstance(entity.kind, EntityKind)
            else entity.kind,
            "calls": json.dumps(list(entity.calls)),
            "called_by": json.dumps(list(entity.called_by)),
            "class_name": entity.class_name or "",
            "exported": entity.exported,
            "sub_kind": entity.sub_kind or "",
            "decorators": json.dumps(list(entity.decorators))
            if entity.decorators
            else "[]",
            "vector": entity.embedding or [],
            "chunk_index": entity.chunk_index,
            "parent_id": entity.parent_id or "",
            "total_chunks": entity.total_chunks,
        }

    def _row_to_entity(self, row: dict) -> CodeEntity:
        calls_raw = row.get("calls", "[]")
        called_by_raw = row.get("called_by", "[]")

        try:
            calls = tuple(json.loads(calls_raw)) if calls_raw else ()
        except (json.JSONDecodeError, TypeError):
            calls = ()
        try:
            called_by = tuple(json.loads(called_by_raw)) if called_by_raw else ()
        except (json.JSONDecodeError, TypeError):
            called_by = ()

        lang_str = row.get("language", "python")
        try:
            language = Language(lang_str)
        except ValueError:
            language = Language.PYTHON

        kind_str = row.get("kind", "function")
        try:
            kind = EntityKind(kind_str)
        except ValueError:
            kind = EntityKind.FUNCTION

        vector = row.get("vector")
        embedding = list(vector) if vector is not None else None

        # Handle missing sub_kind/decorators columns gracefully for backward
        # compatibility with tables created before these fields were added.
        sub_kind: str | None = row.get("sub_kind") or None

        decorators_raw = row.get("decorators", "[]")
        try:
            decorators: tuple[str, ...] = (
                tuple(json.loads(decorators_raw)) if decorators_raw else ()
            )
        except (json.JSONDecodeError, TypeError):
            decorators = ()

        # Handle missing chunk columns gracefully for backward compatibility
        # with tables created before chunking support was added.
        chunk_index = int(row.get("chunk_index") or 0)
        raw_parent_id = row.get("parent_id")
        parent_id: str | None = raw_parent_id if raw_parent_id else None
        total_chunks = int(row.get("total_chunks") or 0)

        return CodeEntity(
            id=row["id"],
            name=row["name"],
            file_path=row["file_path"],
            code=row["code"],
            line_start=int(row["line_start"]),
            line_end=int(row["line_end"]),
            language=language,
            kind=kind,
            calls=calls,
            called_by=called_by,
            class_name=row.get("class_name") or None,
            exported=bool(row.get("exported", False)),
            embedding=embedding,
            sub_kind=sub_kind,
            decorators=decorators,
            chunk_index=chunk_index,
            parent_id=parent_id,
            total_chunks=total_chunks,
        )

    def upsert(self, project_id: str, entities: Sequence[CodeEntity]) -> int:
        """Insert or update entities. Returns count upserted."""
        if not entities:
            return 0

        rows = [self._entity_to_row(project_id, e) for e in entities]

        with self._lock:
            table = self._get_table(project_id)

            if table is None:
                # Create table with data
                db = self._get_db(project_id)
                name = self._table_name(project_id)
                table = db.create_table(name, data=rows, mode="overwrite")
                self._tables[name] = table
            else:
                # Use merge_insert for atomic upsert if available,
                # otherwise fall back to add-then-deduplicate.
                try:
                    (
                        table.merge_insert("id")
                        .when_matched_update_all()
                        .when_not_matched_insert_all()
                        .execute(rows)
                    )
                except (AttributeError, TypeError):
                    # Fallback: delete old rows first, then add new ones.
                    ids = [e.id for e in entities]
                    id_filter = " OR ".join(
                        f'id = "{_escape_lance(eid)}"' for eid in ids
                    )
                    try:
                        table.delete(id_filter)
                    except Exception as e:
                        logger.error("Upsert fallback delete failed: %s", e)
                        raise StorageError(f"Upsert fallback delete failed: {e}") from e
                    table.add(rows)

        return len(entities)

    def search(
        self, project_id: str, query_embedding: list[float], top_k: int
    ) -> list[SearchResult]:
        """Semantic similarity search.

        Returns an empty list when the project has not been indexed yet
        (table does not exist).  Raises ``StorageError`` for unexpected
        failures so callers can return a proper error response.
        """
        table = self._get_table(project_id)
        if table is None:
            # Project not indexed yet — not an error condition.
            return []

        count = table.count_rows()
        if count == 0:
            return []
        actual_k = min(top_k, count)

        try:
            results = table.search(query_embedding).limit(actual_k).to_list()
        except Exception as e:
            logger.exception("LanceDB search failed for project %s", project_id)
            raise StorageError(f"Search index unavailable: {e}") from e

        output = []
        for row in results:
            entity = self._row_to_entity(row)
            distance = row.get("_distance", 0.0)
            # LanceDB uses L2 distance by default; convert to similarity
            similarity = 1.0 / (1.0 + distance)
            output.append(SearchResult(entity=entity, similarity=round(similarity, 4)))

        return output

    def get_by_name(
        self, project_id: str, name: str, file_path: str | None = None
    ) -> list[CodeEntity]:
        """Get entities by name.

        Returns an empty list when the project has not been indexed yet.
        Raises ``StorageError`` for unexpected failures.
        """
        table = self._get_table(project_id)
        if table is None:
            return []
        try:
            where_clause = f'name = "{_escape_lance(name)}"'
            if file_path:
                safe_fp = _sanitize_like_pattern(file_path)
                if safe_fp:
                    where_clause += f" AND file_path LIKE '%{safe_fp}'"
            results = (
                table.search().where(where_clause, prefilter=True).limit(100).to_list()
            )
            return [self._row_to_entity(r) for r in results]
        except Exception as primary_exc:
            # Fallback: scan all rows via pandas
            try:
                df = table.to_pandas()
                matches = df[df["name"] == name]
                if file_path:
                    matches = matches[
                        matches["file_path"].str.endswith(file_path, na=False)
                    ]
                return [
                    self._row_to_entity(row.to_dict()) for _, row in matches.iterrows()
                ]
            except Exception as e:
                logger.exception(
                    "LanceDB get_by_name failed for project %s name %s",
                    project_id,
                    name,
                )
                raise StorageError(f"Search index unavailable: {e}") from primary_exc

    def get_by_names(self, project_id: str, names: Sequence[str]) -> list[CodeEntity]:
        """Get entities matching any of the given names in a single query."""
        if not names:
            return []
        table = self._get_table(project_id)
        if table is None:
            return []
        try:
            conditions = " OR ".join(f'name = "{_escape_lance(n)}"' for n in names)
            results = (
                table.search()
                .where(conditions, prefilter=True)
                .limit(len(names) * 2)
                .to_list()
            )
            return [self._row_to_entity(r) for r in results]
        except Exception as primary_exc:
            try:
                df = table.to_pandas()
                name_set = set(names)
                matches = df[df["name"].isin(name_set)]
                return [
                    self._row_to_entity(row.to_dict()) for _, row in matches.iterrows()
                ]
            except Exception as e:
                logger.exception(
                    "LanceDB get_by_names failed for project %s (%d names)",
                    project_id,
                    len(names),
                )
                raise StorageError(f"Search index unavailable: {e}") from primary_exc

    def get_by_file(self, project_id: str, file_path: str) -> list[CodeEntity]:
        """Get all entities in a file.

        Returns an empty list when the project has not been indexed yet.
        Raises ``StorageError`` for unexpected failures.
        """
        table = self._get_table(project_id)
        if table is None:
            return []
        try:
            results = (
                table.search()
                .where(f'file_path = "{_escape_lance(file_path)}"', prefilter=True)
                .limit(1000)
                .to_list()
            )
            return [self._row_to_entity(r) for r in results]
        except Exception as primary_exc:
            try:
                df = table.to_pandas()
                matches = df[df["file_path"] == file_path]
                return [
                    self._row_to_entity(row.to_dict()) for _, row in matches.iterrows()
                ]
            except Exception as e:
                logger.exception(
                    "LanceDB get_by_file failed for project %s path %s",
                    project_id,
                    file_path,
                )
                raise StorageError(f"Search index unavailable: {e}") from primary_exc

    def get_by_name_substring(
        self, project_id: str, pattern: str, limit: int = 20
    ) -> list[CodeEntity]:
        """Find entities whose name contains *pattern* (case-insensitive).

        The *pattern* is sanitised before being interpolated into the WHERE
        clause so that SQL injection is not possible.  If the LanceDB WHERE
        filter raises an exception the method falls back to an in-memory pandas
        scan.

        Args:
            project_id: The project whose index is queried.
            pattern: Substring to look for inside entity names.
            limit: Maximum number of entities to return.

        Returns:
            Matching :class:`~clean.core.models.CodeEntity` objects, or an
            empty list when the project has not been indexed yet.

        Raises:
            StorageError: When both the WHERE filter and the pandas fallback
                fail unexpectedly.
        """
        table = self._get_table(project_id)
        if table is None:
            return []

        safe = _sanitize_like_pattern(pattern)
        if not safe:
            return []

        where_clause = f"project_id = \"{_escape_lance(project_id)}\" AND lower(name) LIKE '%{safe.lower()}%'"
        try:
            results = (
                table.search()
                .where(where_clause, prefilter=True)
                .limit(limit)
                .to_list()
            )
            return [self._row_to_entity(r) for r in results]
        except Exception as primary_exc:
            # Fallback: scan all rows in-memory via pandas
            try:
                df = table.to_pandas()
                mask = df["name"].str.contains(safe, case=False, na=False)
                return [
                    self._row_to_entity(row.to_dict())
                    for _, row in df[mask].head(limit).iterrows()
                ]
            except Exception as e:
                logger.exception(
                    "LanceDB get_by_name_substring failed for project %s pattern %r",
                    project_id,
                    pattern,
                )
                raise StorageError(f"Search index unavailable: {e}") from primary_exc

    def get_by_file_substring(
        self, project_id: str, pattern: str, limit: int = 20
    ) -> list[CodeEntity]:
        """Find entities whose file_path contains *pattern* (case-insensitive).

        The *pattern* is sanitised before being interpolated into the WHERE
        clause so that SQL injection is not possible.  If the LanceDB WHERE
        filter raises an exception the method falls back to an in-memory pandas
        scan.

        Args:
            project_id: The project whose index is queried.
            pattern: Substring to look for inside entity file paths.
            limit: Maximum number of entities to return.

        Returns:
            Matching :class:`~clean.core.models.CodeEntity` objects, or an
            empty list when the project has not been indexed yet.

        Raises:
            StorageError: When both the WHERE filter and the pandas fallback
                fail unexpectedly.
        """
        table = self._get_table(project_id)
        if table is None:
            return []

        safe = _sanitize_like_pattern(pattern)
        if not safe:
            return []

        where_clause = f"project_id = \"{_escape_lance(project_id)}\" AND lower(file_path) LIKE '%{safe.lower()}%'"
        try:
            results = (
                table.search()
                .where(where_clause, prefilter=True)
                .limit(limit)
                .to_list()
            )
            return [self._row_to_entity(r) for r in results]
        except Exception as primary_exc:
            # Fallback: scan all rows in-memory via pandas
            try:
                df = table.to_pandas()
                mask = df["file_path"].str.contains(safe, case=False, na=False)
                return [
                    self._row_to_entity(row.to_dict())
                    for _, row in df[mask].head(limit).iterrows()
                ]
            except Exception as e:
                logger.exception(
                    "LanceDB get_by_file_substring failed for project %s pattern %r",
                    project_id,
                    pattern,
                )
                raise StorageError(f"Search index unavailable: {e}") from primary_exc

    def delete_by_file(self, project_id: str, file_path: str) -> int:
        """Delete all entities for a file. Returns count deleted."""
        with self._lock:
            table = self._get_table(project_id)
            if table is None:
                return 0
            try:
                before = table.count_rows()
                table.delete(f'file_path = "{_escape_lance(file_path)}"')
                after = table.count_rows()
                return before - after
            except Exception as e:
                logger.error("Delete failed: %s", e)
                return 0

    def get_project_state(self, project_id: str) -> ProjectState | None:
        """Load project state from a metadata table."""
        db = self._get_db(project_id)
        name = self._state_table_name(project_id)
        if name not in self._list_tables(db):
            return None
        try:
            table = db.open_table(name)
            arrow_table = table.to_arrow()
            if arrow_table.num_rows == 0:
                return None
            row = {
                col: arrow_table.column(col)[0].as_py()
                for col in arrow_table.column_names
            }
            files = json.loads(row.get("files_json", "{}"))
            file_states = {k: FileState(**v) for k, v in files.items()}
            return ProjectState(
                project_id=row["project_id"],
                root_path=row["root_path"],
                files=file_states,
                total_entities=int(row.get("total_entities", 0)),
                git_head=row.get("git_head") or None,
            )
        except Exception as e:
            logger.error("Failed to load project state: %s", e)
            return None

    def save_project_state(self, project_id: str, state: ProjectState) -> None:
        """Persist project state to a metadata table."""
        files_dict = {}
        for k, v in state.files.items():
            files_dict[k] = {
                "file_path": v.file_path,
                "content_hash": v.content_hash,
                "entity_count": v.entity_count,
                "last_indexed_at": v.last_indexed_at,
            }

        row = {
            "project_id": state.project_id,
            "root_path": state.root_path,
            "files_json": json.dumps(files_dict),
            "total_entities": state.total_entities,
            "git_head": state.git_head or "",
        }

        with self._lock:
            db = self._get_db(project_id)
            name = self._state_table_name(project_id)
            db.create_table(name, data=[row], mode="overwrite")

    def count(self, project_id: str) -> int:
        """Count entities for a project."""
        table = self._get_table(project_id)
        if table is None:
            return 0
        return table.count_rows()

    def clear(self, project_id: str) -> None:
        """Remove all entities for a project."""
        with self._lock:
            db = self._get_db(project_id)
            name = self._table_name(project_id)
            if name in self._list_tables(db):
                db.drop_table(name)
                self._tables.pop(name, None)

            state_name = self._state_table_name(project_id)
            if state_name in self._list_tables(db):
                db.drop_table(state_name)

    # ── Rebuild-table helpers (force-reindex isolation) ─────────────────────

    def _rebuild_table_name(self, project_id: str) -> str:
        """Return the name of the in-progress rebuild table for *project_id*."""
        return f"rebuild_{project_id.replace('/', '_').replace('.', '_')}"

    def clear_for_rebuild(self, project_id: str, dimension: int) -> None:
        """Create (or recreate) a fresh rebuild table for *project_id*.

        Searches on the live ``entities_*`` table continue without interruption
        while the rebuild table is being populated.
        """
        db = self._get_db(project_id)
        rebuild_name = self._rebuild_table_name(project_id)
        with self._lock:
            # Drop any leftover rebuild table from a previous aborted run.
            if rebuild_name in self._list_tables(db):
                db.drop_table(rebuild_name)
                self._tables.pop(rebuild_name, None)

            schema = ENTITY_SCHEMA.set(
                ENTITY_SCHEMA.get_field_index("vector"),
                pa.field("vector", pa.list_(pa.float32(), dimension)),
            )
            table = db.create_table(rebuild_name, schema=schema)
            self._tables[rebuild_name] = table
            logger.info("Created rebuild table '%s' (dim=%d)", rebuild_name, dimension)

    def upsert_to_rebuild(self, project_id: str, entities: Sequence[CodeEntity]) -> int:
        """Write entities into the rebuild staging table.

        Uses the same upsert logic as the live table so the caller does not
        need to know which table is the target.
        """
        if not entities:
            return 0

        rebuild_name = self._rebuild_table_name(project_id)
        rows = [self._entity_to_row(project_id, e) for e in entities]

        with self._lock:
            table = self._tables.get(rebuild_name)
            if table is None:
                db = self._get_db(project_id)
                if rebuild_name not in self._list_tables(db):
                    raise StorageError(
                        f"Rebuild table for {project_id} does not exist. "
                        "Call clear_for_rebuild() first."
                    )
                table = db.open_table(rebuild_name)
                self._tables[rebuild_name] = table

            try:
                (
                    table.merge_insert("id")
                    .when_matched_update_all()
                    .when_not_matched_insert_all()
                    .execute(rows)
                )
            except (AttributeError, TypeError):
                table.add(rows)

        return len(entities)

    def swap_rebuild_table(self, project_id: str) -> None:
        """Atomically swap the rebuild table to become the live entity table.

        The RLock is held only for the metadata operations (drop + rename or
        copy), not across the full data ingestion, so read latency impact is
        minimal.

        LanceDB does not expose a native table-rename API, so the swap is
        implemented as:
          1. Drop the old live table.
          2. Create a new live table populated from the rebuild table's data.
          3. Drop the rebuild table.

        All three steps are guarded by the RLock so no concurrent search can
        observe the old live table being absent.
        """
        db = self._get_db(project_id)
        live_name = self._table_name(project_id)
        rebuild_name = self._rebuild_table_name(project_id)

        with self._lock:
            if rebuild_name not in self._list_tables(db):
                raise StorageError(f"Rebuild table for {project_id} does not exist.")

            rebuild_table = self._tables.get(rebuild_name) or db.open_table(
                rebuild_name
            )

            # Read all rebuild data while still under the lock.
            try:
                arrow_data = rebuild_table.to_arrow()
            except Exception as e:
                raise StorageError(
                    f"Failed to read rebuild table for {project_id}: {e}"
                ) from e

            # Drop live table if it exists.
            if live_name in self._list_tables(db):
                db.drop_table(live_name)
                self._tables.pop(live_name, None)

            # Create new live table from rebuild data.
            if arrow_data.num_rows > 0:
                new_table = db.create_table(live_name, data=arrow_data)
            else:
                # No rows — create an empty schema-only table.
                new_table = db.create_table(live_name, schema=arrow_data.schema)
            self._tables[live_name] = new_table

            # Drop rebuild table.
            db.drop_table(rebuild_name)
            self._tables.pop(rebuild_name, None)

            logger.info(
                "Swapped rebuild table to live for project '%s' (%d rows)",
                project_id,
                arrow_data.num_rows,
            )

    def copy_table(
        self,
        source_project_id: str,
        dest_project_id: str,
        new_root_path: str | None = None,
    ) -> bool:
        """Copy entity and state tables from one project to another.

        Used to bootstrap a branch index from the default branch's vectors.
        Rewrites ``project_id`` fields in the copied data. If ``new_root_path``
        is provided, also rewrites the ``root_path`` column and rebases every
        file-path key in ``files_json`` from the source root to the new root.

        Args:
            source_project_id: Project ID whose tables will be read.
            dest_project_id: Project ID that will receive the copied tables.
            new_root_path: When provided, the state table's ``root_path`` and
                every file-path key in ``files_json`` are rebased from the
                source root path to this value.

        Returns:
            True if the copy succeeded (including the case where the source
            entity table exists but contains zero rows). False if the source
            entity table does not exist.
        """
        db = self._get_db(source_project_id)
        src_entity_name = self._table_name(source_project_id)
        dest_entity_name = self._table_name(dest_project_id)

        # ── 1. Entity table ──────────────────────────────────────────────────
        if src_entity_name not in self._list_tables(db):
            logger.warning(
                "copy_table: source entity table '%s' does not exist",
                src_entity_name,
            )
            return False

        try:
            src_entity_table = db.open_table(src_entity_name)
            arrow_data = src_entity_table.to_arrow()

            if arrow_data.num_rows > 0:
                pid_col = pa.array(
                    [dest_project_id] * arrow_data.num_rows, type=pa.utf8()
                )
                arrow_data = arrow_data.set_column(
                    arrow_data.schema.get_field_index("project_id"),
                    "project_id",
                    pid_col,
                )

            with self._lock:
                if arrow_data.num_rows > 0:
                    dest_table = db.create_table(
                        dest_entity_name, data=arrow_data, mode="overwrite"
                    )
                else:
                    dest_table = db.create_table(
                        dest_entity_name, schema=arrow_data.schema, mode="overwrite"
                    )
                self._tables[dest_entity_name] = dest_table

            logger.info(
                "copy_table: copied entity table '%s' -> '%s' (%d rows)",
                src_entity_name,
                dest_entity_name,
                arrow_data.num_rows,
            )
        except Exception as e:
            logger.error(
                "copy_table: failed to copy entity table '%s' -> '%s': %s",
                src_entity_name,
                dest_entity_name,
                e,
            )
            return False

        # ── 2. State table ───────────────────────────────────────────────────
        src_state_name = self._state_table_name(source_project_id)
        dest_state_name = self._state_table_name(dest_project_id)

        if src_state_name not in self._list_tables(db):
            # State table is optional; entity copy already succeeded.
            logger.debug(
                "copy_table: source state table '%s' not found; skipping",
                src_state_name,
            )
            return True

        try:
            src_state_table = db.open_table(src_state_name)
            state_arrow = src_state_table.to_arrow()

            if state_arrow.num_rows > 0:
                # Rewrite project_id column.
                pid_col = pa.array(
                    [dest_project_id] * state_arrow.num_rows, type=pa.utf8()
                )
                state_arrow = state_arrow.set_column(
                    state_arrow.schema.get_field_index("project_id"),
                    "project_id",
                    pid_col,
                )

                if new_root_path is not None:
                    # Determine old root path from the source state table.
                    old_root_path: str = (
                        state_arrow.column("root_path")[0].as_py() or ""
                    )

                    # Rewrite root_path column.
                    rp_col = pa.array(
                        [new_root_path] * state_arrow.num_rows, type=pa.utf8()
                    )
                    state_arrow = state_arrow.set_column(
                        state_arrow.schema.get_field_index("root_path"),
                        "root_path",
                        rp_col,
                    )

                    # Rewrite file-path keys inside files_json.
                    new_files_json_values: list[str] = []
                    for raw in state_arrow.column("files_json").to_pylist():
                        try:
                            files: dict = json.loads(raw or "{}")
                        except (json.JSONDecodeError, TypeError):
                            files = {}

                        rebased: dict = {}
                        for path_key, file_state in files.items():
                            if old_root_path and path_key.startswith(old_root_path):
                                new_key = new_root_path + path_key[len(old_root_path) :]
                                # Also update the file_path field inside the state
                                # value so it stays consistent.
                                if (
                                    isinstance(file_state, dict)
                                    and "file_path" in file_state
                                    and isinstance(file_state["file_path"], str)
                                    and file_state["file_path"].startswith(
                                        old_root_path
                                    )
                                ):
                                    file_state = dict(file_state)
                                    file_state["file_path"] = (
                                        new_root_path
                                        + file_state["file_path"][len(old_root_path) :]
                                    )
                            else:
                                new_key = path_key
                            rebased[new_key] = file_state

                        new_files_json_values.append(json.dumps(rebased))

                    fj_col = pa.array(new_files_json_values, type=pa.utf8())
                    state_arrow = state_arrow.set_column(
                        state_arrow.schema.get_field_index("files_json"),
                        "files_json",
                        fj_col,
                    )

            with self._lock:
                if state_arrow.num_rows > 0:
                    db.create_table(dest_state_name, data=state_arrow, mode="overwrite")
                else:
                    db.create_table(
                        dest_state_name, schema=state_arrow.schema, mode="overwrite"
                    )

            logger.info(
                "copy_table: copied state table '%s' -> '%s'",
                src_state_name,
                dest_state_name,
            )
        except Exception as e:
            logger.error(
                "copy_table: failed to copy state table '%s' -> '%s': %s",
                src_state_name,
                dest_state_name,
                e,
            )
            return False

        return True
