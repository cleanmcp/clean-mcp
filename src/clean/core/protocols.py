"""Protocol definitions for Clean components."""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from .models import CodeEntity, ProjectState, SearchResult
from .types import Language


@runtime_checkable
class VectorStore(Protocol):
    def initialize(self, project_id: str, dimension: int) -> None: ...
    def upsert(self, project_id: str, entities: Sequence[CodeEntity]) -> int: ...
    def search(
        self, project_id: str, query_embedding: list[float], top_k: int
    ) -> list[SearchResult]: ...
    def get_by_name(
        self, project_id: str, name: str, file_path: str | None = None
    ) -> list[CodeEntity]: ...
    def get_by_names(
        self, project_id: str, names: Sequence[str]
    ) -> list[CodeEntity]: ...
    def get_by_file(self, project_id: str, file_path: str) -> list[CodeEntity]: ...
    def get_by_name_substring(
        self, project_id: str, pattern: str, limit: int = 20
    ) -> list[CodeEntity]: ...
    def get_by_file_substring(
        self, project_id: str, pattern: str, limit: int = 20
    ) -> list[CodeEntity]: ...
    def delete_by_file(self, project_id: str, file_path: str) -> int: ...
    def get_project_state(self, project_id: str) -> ProjectState | None: ...
    def save_project_state(self, project_id: str, state: ProjectState) -> None: ...
    def count(self, project_id: str) -> int: ...
    def clear(self, project_id: str) -> None: ...


@runtime_checkable
class Embedder(Protocol):
    dimension: int

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]: ...
    def embed_query(self, query: str) -> list[float]: ...


@runtime_checkable
class LanguageParser(Protocol):
    language: Language
    extensions: list[str]

    def parse_file(self, file_path: str, source: bytes) -> list[CodeEntity]: ...


@runtime_checkable
class Formatter(Protocol):
    def format_results(
        self, results: list[SearchResult], context: dict | None = None
    ) -> str: ...
