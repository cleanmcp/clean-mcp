"""CodebaseIndexer — orchestrates parse → embed → store → callgraph."""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Callable

from ..core.config import CleanConfig
from ..core.models import CodeEntity, FileState, ProjectState
from ..core.protocols import Embedder, VectorStore
from ..core.types import EntityKind, Language
from ..parsing.registry import ParserRegistry
from ..util.hashing import hash_file
from ..util.logging import get_logger
from ..util.security import sanitize_path, MAX_FILE_SIZE_MB
from .call_graph import CallGraphBuilder
from .file_scanner import FileScanner
from .incremental import IncrementalIndexer, ChangeSet

logger = get_logger(__name__)

# --- Metadata detection helpers ---

_EXT_TO_LANGUAGE: dict[str, str] = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".rb": "Ruby",
    ".cpp": "C++",
    ".cc": "C++",
    ".h": "C++",
    ".c": "C",
    ".swift": "Swift",
    ".kt": "Kotlin",
    ".php": "PHP",
    ".dart": "Dart",
}

_EXT_TO_LANGUAGE_ENUM: dict[str, Language] = {
    ".py": Language.PYTHON,
    ".js": Language.JAVASCRIPT,
    ".jsx": Language.JAVASCRIPT,
    ".mjs": Language.JAVASCRIPT,
    ".cjs": Language.JAVASCRIPT,
    ".ts": Language.TYPESCRIPT,
    ".tsx": Language.TYPESCRIPT,
}

# Framework markers: file in repo root → list of (key_in_content_or_None, framework_tag)
# For package.json / requirements.txt we inspect content; others are presence-only.
_CARGO_TOML = "Cargo.toml"
_GO_MOD = "go.mod"
_GEMFILE = "Gemfile"
_POM_XML = "pom.xml"
_BUILD_GRADLE = "build.gradle"

_JS_FRAMEWORKS = {
    "express": "Express",
    "fastify": "Fastify",
    "hono": "Hono",
    "react": "React",
    "next": "Next.js",
    "vue": "Vue",
    "angular": "Angular",
    "svelte": "Svelte",
    "nuxt": "Nuxt",
    "remix": "Remix",
    "koa": "Koa",
    "nestjs": "NestJS",
    "@nestjs/core": "NestJS",
}

_PYTHON_FRAMEWORKS = {
    "fastapi": "FastAPI",
    "django": "Django",
    "flask": "Flask",
    "starlette": "Starlette",
    "tornado": "Tornado",
    "aiohttp": "aiohttp",
    "litestar": "Litestar",
    "sanic": "Sanic",
}


def detect_repo_metadata(
    repo_root: str, indexed_files: list[str]
) -> dict[str, str | None]:
    """Detect primary language, tags, and description for a repository.

    Args:
        repo_root: Absolute path to the cloned repository root.
        indexed_files: List of file paths that were indexed.

    Returns:
        Dict with keys ``description``, ``primary_language``, and ``tags``.
        All values may be ``None`` if detection is unsuccessful.
        ``tags`` is a JSON-encoded list string when present.
    """
    root = Path(repo_root)

    # 1. Primary language — count extensions across all indexed files
    ext_counts: Counter[str] = Counter()
    for fp in indexed_files:
        _, ext = os.path.splitext(fp)
        if ext.lower() in _EXT_TO_LANGUAGE:
            ext_counts[ext.lower()] += 1

    primary_language: str | None = None
    if ext_counts:
        top_ext = ext_counts.most_common(1)[0][0]
        primary_language = _EXT_TO_LANGUAGE[top_ext]

    # 2. Tags — start with primary language, then add framework markers
    tags: list[str] = []
    if primary_language:
        tags.append(primary_language)

    # Check package.json (Node/JS frameworks)
    pkg_json = root / "package.json"
    if pkg_json.is_file():
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
            all_deps: dict = {}
            all_deps.update(data.get("dependencies", {}))
            all_deps.update(data.get("devDependencies", {}))
            for dep_key, tag_name in _JS_FRAMEWORKS.items():
                if dep_key in all_deps and tag_name not in tags:
                    tags.append(tag_name)
        except Exception:
            pass

    # Check pyproject.toml and requirements.txt (Python frameworks)
    py_deps_text = ""
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            py_deps_text += pyproject.read_text(
                encoding="utf-8", errors="replace"
            ).lower()
        except Exception:
            pass
    for req_file in (
        "requirements.txt",
        "requirements-dev.txt",
        "requirements/base.txt",
    ):
        req_path = root / req_file
        if req_path.is_file():
            try:
                py_deps_text += req_path.read_text(
                    encoding="utf-8", errors="replace"
                ).lower()
            except Exception:
                pass
    if py_deps_text:
        for dep_key, tag_name in _PYTHON_FRAMEWORKS.items():
            if dep_key in py_deps_text and tag_name not in tags:
                tags.append(tag_name)

    # Presence-only markers
    if (root / _CARGO_TOML).is_file() and "Rust" not in tags:
        tags.append("Rust")
    if (root / _GO_MOD).is_file() and "Go" not in tags:
        tags.append("Go")
    if (root / _GEMFILE).is_file():
        if "Ruby" not in tags:
            tags.append("Ruby")
        gemfile_text = ""
        try:
            gemfile_text = (
                (root / _GEMFILE).read_text(encoding="utf-8", errors="replace").lower()
            )
        except Exception:
            pass
        if "rails" in gemfile_text and "Rails" not in tags:
            tags.append("Rails")
    if (root / _POM_XML).is_file() or (root / _BUILD_GRADLE).is_file():
        if "Java" not in tags:
            tags.append("Java")
        # Detect Spring
        spring_text = ""
        for marker in (_POM_XML, _BUILD_GRADLE):
            marker_path = root / marker
            if marker_path.is_file():
                try:
                    spring_text += marker_path.read_text(
                        encoding="utf-8", errors="replace"
                    ).lower()
                except Exception:
                    pass
        if "spring" in spring_text and "Spring" not in tags:
            tags.append("Spring")

    tags_json: str | None = json.dumps(tags) if tags else None

    # 3. Description — first non-blank line of README.md
    description: str | None = None
    for readme_name in ("README.md", "Readme.md", "readme.md"):
        readme = root / readme_name
        if readme.is_file():
            try:
                for line in readme.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    # Strip markdown heading prefix
                    if line.startswith("#"):
                        line = line.lstrip("#").strip()
                    if line:
                        description = line
                    break
            except Exception:
                pass
            break

    return {
        "description": description,
        "primary_language": primary_language,
        "tags": tags_json,
    }


# Progress callback type: (phase, progress%, files_processed, files_total, entities, current_file) -> continue
ProgressCallback = Callable[[str, int, int, int, int, str], bool]


class CodebaseIndexer:
    """Orchestrates the full indexing pipeline."""

    def __init__(
        self,
        store: VectorStore,
        embedder: Embedder,
        parser_registry: ParserRegistry,
        scanner: FileScanner,
        call_graph: CallGraphBuilder,
        incremental: IncrementalIndexer,
        config: CleanConfig,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._parsers = parser_registry
        self._scanner = scanner
        self._call_graph = call_graph
        self._incremental = incremental
        self._config = config

    def index(
        self,
        path: str,
        force_full: bool = False,
        project_id: str | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> dict:
        """
        Index a codebase. Returns a result dict.

        If project was indexed before and force_full is False,
        performs incremental indexing (only changed files).

        Optional project_id overrides the default path-based ID
        (useful for API layer where owner/repo avoids basename collisions).

        progress_callback: Optional callback for progress updates.
            Signature: (phase, progress%, files_processed, files_total, entities, current_file) -> bool
            Return False to cancel the indexing operation.
        """
        try:
            path = sanitize_path(path)
        except Exception as e:
            return self._error_result(str(e), path)

        abs_path = os.path.abspath(path)

        if not os.path.isdir(abs_path):
            return self._error_result(f"Not a directory: {abs_path}", abs_path)

        project_id = project_id or self._project_id(abs_path)
        self._store.initialize(project_id, self._embedder.dimension)

        # Helper to call progress callback and check for cancellation
        def report_progress(
            phase: str,
            progress: int,
            files_proc: int = 0,
            files_tot: int = 0,
            entities: int = 0,
            current_file: str = "",
        ) -> bool:
            if progress_callback:
                return progress_callback(
                    phase, progress, files_proc, files_tot, entities, current_file
                )
            return True

        # 1. Scan for files
        logger.info("Scanning files in %s...", abs_path)
        if not report_progress("scanning", 0, 0, 0, 0, abs_path):
            return self._cancelled_result(abs_path)
        all_files = self._scanner.scan(abs_path)
        logger.info("Found %d indexable files", len(all_files))
        if not report_progress("scanning", 100, 0, len(all_files), 0, ""):
            return self._cancelled_result(abs_path)

        if not all_files:
            return {
                "status": "success",
                "files_processed": 0,
                "functions_indexed": 0,
                "path": abs_path,
                "incremental": False,
            }

        # 2. Detect changes (or full index)
        # For force-reindex we build into a rebuild staging table so that
        # concurrent searches continue reading the live table until the new
        # index is fully ready, then we atomically swap.
        _using_rebuild_table = False
        if force_full:
            changes = ChangeSet(added=all_files)
            self._store.clear_for_rebuild(project_id, self._embedder.dimension)
            _using_rebuild_table = True
        else:
            changes = self._incremental.detect_changes(project_id, abs_path, all_files)

        files_to_process = changes.added + changes.modified
        is_incremental = bool(changes.unchanged) and not force_full

        if not files_to_process and not changes.deleted:
            logger.info("No changes detected, skipping re-index")
            return {
                "status": "success",
                "files_processed": 0,
                "functions_indexed": self._store.count(project_id),
                "path": abs_path,
                "incremental": is_incremental,
            }

        # 3. Delete removed files from store
        for deleted_file in changes.deleted:
            self._store.delete_by_file(project_id, deleted_file)
            logger.debug("Deleted entities for removed file: %s", deleted_file)

        # 4. Delete modified files (will re-add)
        for modified_file in changes.modified:
            self._store.delete_by_file(project_id, modified_file)

        # 5. Parse files
        logger.info("Parsing %d files...", len(files_to_process))
        all_entities: list[CodeEntity] = []
        files_processed = 0
        total_files = len(files_to_process)

        for i, filepath in enumerate(files_to_process):
            # Report progress before processing each file
            progress_pct = int((i / total_files) * 100) if total_files else 100
            if not report_progress(
                "parsing",
                progress_pct,
                files_processed,
                total_files,
                len(all_entities),
                filepath,
            ):
                return self._cancelled_result(abs_path)

            _, ext = os.path.splitext(filepath)
            parser = self._parsers.get_parser(ext)
            if parser is None:
                continue

            try:
                # TOCTOU re-check: verify file size at read time
                max_bytes = int(MAX_FILE_SIZE_MB * 1024 * 1024)
                with open(filepath, "rb") as f:
                    source = f.read(max_bytes + 1)
                    if len(source) > max_bytes:
                        logger.warning(
                            "File exceeds size limit at read time: %s", filepath
                        )
                        continue
                try:
                    source.decode("utf-8")
                except UnicodeDecodeError:
                    logger.warning("Skipping non-UTF-8 file: %s", filepath)
                    continue
                entities = parser.parse_file(filepath, source)
                all_entities.extend(entities)
                files_processed += 1
            except Exception as e:
                logger.warning("Error parsing %s: %s", filepath, e)

        logger.info(
            "Parsed %d entities from %d files", len(all_entities), files_processed
        )
        if not report_progress(
            "parsing", 100, files_processed, total_files, len(all_entities), ""
        ):
            return self._cancelled_result(abs_path)

        # Generate one synthetic FILE_SUMMARY entity per parsed file
        file_summaries = self._generate_file_summaries(all_entities, abs_path)
        if file_summaries:
            all_entities.extend(file_summaries)
            logger.info("Generated %d file summary entities", len(file_summaries))

        # Chunk large entities so each chunk gets a focused embedding vector
        from .chunker import chunk_entities  # noqa: PLC0415

        pre_chunk_count = len(all_entities)
        all_entities = chunk_entities(all_entities)
        if len(all_entities) != pre_chunk_count:
            logger.info(
                "Chunking: %d entities expanded to %d after splitting large entities",
                pre_chunk_count,
                len(all_entities),
            )

        if not all_entities:
            if _using_rebuild_table:
                # Even with no entities, swap so that a force-reindex clears
                # the live table (empty rebuild replaces old data).
                self._store.swap_rebuild_table(project_id)
            self._save_state(project_id, abs_path, files_to_process, all_files)
            return {
                "status": "success",
                "files_processed": files_processed,
                "functions_indexed": self._store.count(project_id),
                "path": abs_path,
                "incremental": is_incremental,
            }

        # 6. Embed
        logger.info("Embedding %d entities...", len(all_entities))
        codes = [e.code for e in all_entities]
        batch_size = self._config.indexer.batch_size
        embeddings: list[list[float]] = []
        total_batches = (len(codes) + batch_size - 1) // batch_size if codes else 1

        for i in range(0, len(codes), batch_size):
            batch_idx = i // batch_size
            progress_pct = (
                int((batch_idx / total_batches) * 100) if total_batches else 100
            )
            if not report_progress(
                "embedding",
                progress_pct,
                files_processed,
                total_files,
                len(all_entities),
                "",
            ):
                return self._cancelled_result(abs_path)

            batch = codes[i : i + batch_size]
            embeddings.extend(self._embedder.embed_batch(batch))

        all_entities = [
            e.with_embedding(emb) for e, emb in zip(all_entities, embeddings)
        ]
        if not report_progress(
            "embedding", 100, files_processed, total_files, len(all_entities), ""
        ):
            return self._cancelled_result(abs_path)

        # 7. Compute call graph
        logger.info("Computing call graph...")
        if not report_progress(
            "computing_relations",
            0,
            files_processed,
            total_files,
            len(all_entities),
            "",
        ):
            return self._cancelled_result(abs_path)

        # Get existing entities for full graph computation
        existing = []
        if is_incremental:
            for f in changes.unchanged:
                existing.extend(self._store.get_by_file(project_id, f))

        all_for_graph = list(all_entities) + existing
        all_for_graph = self._call_graph.compute(project_id, all_for_graph)

        # Separate new/modified entities from existing
        new_ids = {e.id for e in all_entities}
        new_entities = [e for e in all_for_graph if e.id in new_ids]
        updated_existing = [e for e in all_for_graph if e.id not in new_ids]

        if not report_progress(
            "computing_relations",
            100,
            files_processed,
            total_files,
            len(all_entities),
            "",
        ):
            return self._cancelled_result(abs_path)

        # 8. Store new entities
        logger.info("Storing %d entities...", len(new_entities))
        if not report_progress(
            "storing", 0, files_processed, total_files, len(all_entities), ""
        ):
            if _using_rebuild_table:
                # Clean up the rebuild table on cancellation
                try:
                    self._store.clear(project_id + "_rebuild_cleanup")
                except Exception:
                    pass
            return self._cancelled_result(abs_path)

        if _using_rebuild_table:
            # Write into the rebuild staging table (live table is still readable)
            self._store.upsert_to_rebuild(project_id, new_entities)
            if updated_existing:
                self._store.upsert_to_rebuild(project_id, updated_existing)
        else:
            self._store.upsert(project_id, new_entities)
            # Update existing entities with new called_by info
            if updated_existing:
                self._store.upsert(project_id, updated_existing)

        if not report_progress(
            "storing", 100, files_processed, total_files, len(all_entities), ""
        ):
            return self._cancelled_result(abs_path)

        # For force-reindex: atomically swap rebuild → live so reads are never
        # interrupted during the data ingestion phase.
        if _using_rebuild_table:
            logger.info("Swapping rebuild table to live for %s", project_id)
            self._store.swap_rebuild_table(project_id)

        # 9. Save project state
        self._save_state(project_id, abs_path, files_to_process, all_files)

        total = self._store.count(project_id)
        logger.info(
            "Indexing complete: %d entities from %d files", total, files_processed
        )

        return {
            "status": "success",
            "files_processed": files_processed,
            "functions_indexed": total,
            "path": abs_path,
            "incremental": is_incremental,
        }

    @staticmethod
    def _generate_file_summaries(
        entities: list[CodeEntity], project_path: str
    ) -> list[CodeEntity]:
        """Generate one FILE_SUMMARY entity per file in the entity list.

        Args:
            entities: All parsed entities (before embedding).
            project_path: Absolute path to the project root (used to produce
                relative display paths in the summary text).

        Returns:
            A list of synthetic FILE_SUMMARY CodeEntity objects.
        """
        # Group entity names and kinds by file_path
        file_entity_names: dict[str, list[str]] = {}
        file_entity_kinds: dict[str, list[str]] = {}
        file_language: dict[str, Language] = {}

        for entity in entities:
            fp = entity.file_path
            if fp not in file_entity_names:
                file_entity_names[fp] = []
                file_entity_kinds[fp] = []
            file_entity_names[fp].append(entity.name)
            file_entity_kinds[fp].append(entity.kind.value)
            # Use the language from the first entity seen for this file
            if fp not in file_language:
                file_language[fp] = entity.language

        summaries: list[CodeEntity] = []
        for fp, names in file_entity_names.items():
            kinds = file_entity_kinds[fp]

            # Build a kind-count summary (e.g. "3 functions, 1 class")
            kind_counts: Counter[str] = Counter(kinds)
            kind_labels: list[str] = []
            kind_display = {
                "function": "function",
                "arrow_function": "arrow function",
                "method": "method",
                "class": "class",
                "interface": "interface",
                "type": "type",
                "export_const": "exported constant",
                "enum": "enum",
                "file_summary": "file summary",
            }
            for kind_val, count in kind_counts.most_common():
                label = kind_display.get(kind_val, kind_val)
                kind_labels.append(f"{count} {label}{'s' if count != 1 else ''}")

            # Produce a relative display path when possible
            try:
                display_path = os.path.relpath(fp, project_path)
            except ValueError:
                display_path = fp

            filename = os.path.basename(fp)

            # Deduplicate names while preserving insertion order
            seen_names: set[str] = set()
            unique_names: list[str] = []
            for n in names:
                if n not in seen_names:
                    seen_names.add(n)
                    unique_names.append(n)

            summary_text = (
                f"File: {display_path}\n"
                f"Contains: {', '.join(kind_labels)}\n"
                f"Entities: {', '.join(unique_names)}"
            )

            # Determine language from extension if not already known
            _, ext = os.path.splitext(fp)
            language = file_language.get(fp) or _EXT_TO_LANGUAGE_ENUM.get(
                ext.lower(), Language.PYTHON
            )

            summaries.append(
                CodeEntity(
                    name=filename,
                    file_path=fp,
                    code=summary_text,
                    line_start=0,
                    line_end=0,
                    language=language,
                    kind=EntityKind.FILE_SUMMARY,
                    calls=(),
                    called_by=(),
                    exported=False,
                )
            )

        return summaries

    def _save_state(
        self,
        project_id: str,
        root_path: str,
        processed_files: list[str],
        all_files: list[str],
    ) -> None:
        """Save project state for incremental indexing."""
        existing_state = self._store.get_project_state(project_id)
        file_states = existing_state.files if existing_state else {}

        now = time.time()
        for f in processed_files:
            try:
                content_hash = hash_file(f)
                entities = self._store.get_by_file(project_id, f)
                file_states[f] = FileState(
                    file_path=f,
                    content_hash=content_hash,
                    entity_count=len(entities),
                    last_indexed_at=now,
                )
            except Exception:
                pass

        # Remove deleted files from state
        current_set = set(all_files)
        for f in list(file_states.keys()):
            if f not in current_set:
                del file_states[f]

        state = ProjectState(
            project_id=project_id,
            root_path=root_path,
            files=file_states,
            total_entities=self._store.count(project_id),
            git_head=IncrementalIndexer.get_git_head(root_path),
        )
        self._store.save_project_state(project_id, state)

    @staticmethod
    def _project_id(path: str) -> str:
        """Generate a project ID from path."""
        return os.path.basename(path).lower().replace(" ", "_")

    @staticmethod
    def _error_result(error: str, path: str) -> dict:
        return {
            "status": "error",
            "error": error,
            "files_processed": 0,
            "functions_indexed": 0,
            "path": path,
        }

    @staticmethod
    def _cancelled_result(path: str) -> dict:
        return {
            "status": "cancelled",
            "error": "Indexing was cancelled",
            "files_processed": 0,
            "functions_indexed": 0,
            "path": path,
        }
