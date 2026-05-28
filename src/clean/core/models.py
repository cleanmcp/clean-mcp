"""Core data models for Clean."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .types import EntityKind, Language


@dataclass(frozen=True, slots=True)
class CodeEntity:
    """A parsed code entity (function, class, method, etc.)."""

    name: str
    file_path: str
    code: str
    line_start: int
    line_end: int
    language: Language
    kind: EntityKind
    calls: tuple[str, ...] = ()
    called_by: tuple[str, ...] = ()
    class_name: str | None = None
    exported: bool = False
    embedding: list[float] | None = None
    id: str = ""
    sub_kind: str | None = None
    decorators: tuple[str, ...] = ()
    chunk_index: int = 0
    parent_id: str | None = None
    total_chunks: int = 0

    def __post_init__(self) -> None:
        if not self.id:
            raw = f"{self.file_path}:{self.name}:{self.line_start}"
            computed = hashlib.sha256(raw.encode()).hexdigest()[:32]
            object.__setattr__(self, "id", computed)

    def with_embedding(self, embedding: list[float]) -> CodeEntity:
        """Return a copy with the embedding set."""
        return CodeEntity(
            id=self.id,
            name=self.name,
            file_path=self.file_path,
            code=self.code,
            line_start=self.line_start,
            line_end=self.line_end,
            language=self.language,
            kind=self.kind,
            calls=self.calls,
            called_by=self.called_by,
            class_name=self.class_name,
            exported=self.exported,
            embedding=embedding,
            sub_kind=self.sub_kind,
            decorators=self.decorators,
            chunk_index=self.chunk_index,
            parent_id=self.parent_id,
            total_chunks=self.total_chunks,
        )

    def with_called_by(self, called_by: tuple[str, ...]) -> CodeEntity:
        """Return a copy with called_by set."""
        return CodeEntity(
            id=self.id,
            name=self.name,
            file_path=self.file_path,
            code=self.code,
            line_start=self.line_start,
            line_end=self.line_end,
            language=self.language,
            kind=self.kind,
            calls=self.calls,
            called_by=called_by,
            class_name=self.class_name,
            exported=self.exported,
            embedding=self.embedding,
            sub_kind=self.sub_kind,
            decorators=self.decorators,
            chunk_index=self.chunk_index,
            parent_id=self.parent_id,
            total_chunks=self.total_chunks,
        )

    def with_file_path(self, file_path: str) -> CodeEntity:
        """Return a copy with file_path set."""
        return CodeEntity(
            id=self.id,
            name=self.name,
            file_path=file_path,
            code=self.code,
            line_start=self.line_start,
            line_end=self.line_end,
            language=self.language,
            kind=self.kind,
            calls=self.calls,
            called_by=self.called_by,
            class_name=self.class_name,
            exported=self.exported,
            embedding=self.embedding,
            sub_kind=self.sub_kind,
            decorators=self.decorators,
            chunk_index=self.chunk_index,
            parent_id=self.parent_id,
            total_chunks=self.total_chunks,
        )


@dataclass
class FileState:
    """Tracks the state of an indexed file."""

    file_path: str
    content_hash: str
    entity_count: int
    last_indexed_at: float


@dataclass
class ProjectState:
    """Tracks the state of an indexed project."""

    project_id: str
    root_path: str
    files: dict[str, FileState] = field(default_factory=dict)
    total_entities: int = 0
    git_head: str | None = None


@dataclass(frozen=True, slots=True)
class SearchResult:
    """A single search result."""

    entity: CodeEntity
    similarity: float


@dataclass
class SearchContext:
    """Expanded context around a search result."""

    function: CodeEntity | None = None
    callees: list[CodeEntity] = field(default_factory=list)
    callers: list[CodeEntity] = field(default_factory=list)
    same_file: list[CodeEntity] = field(default_factory=list)
