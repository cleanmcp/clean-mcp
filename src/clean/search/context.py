"""Context expansion — caller/callee/same-file discovery."""

from __future__ import annotations

from ..core.models import CodeEntity, SearchContext
from ..core.protocols import VectorStore
from ..util.logging import get_logger

logger = get_logger(__name__)

MAX_CONTEXT_ENTITIES = 50


class ContextExpander:
    """Expand search results with relationship context."""

    def __init__(self, store: VectorStore) -> None:
        self._store = store

    def expand(
        self,
        project_id: str,
        func_name: str,
        depth: int = 1,
        file_path: str | None = None,
    ) -> SearchContext:
        """
        Get context around a function: callees, callers, same-file entities.

        Uses batch queries per depth level instead of individual lookups,
        reducing total queries from O(branching^depth) to O(depth).

        Args:
            project_id: The project to search within.
            func_name: Name of the function to expand context for.
            depth: How many levels of call-graph to traverse.
            file_path: Optional file path to disambiguate when multiple
                functions share the same name.
        """
        entities = self._store.get_by_name(project_id, func_name, file_path=file_path)
        if not entities:
            return SearchContext()

        main = entities[0]
        ctx = SearchContext(function=main)

        # Batch expand callees — one query per depth level
        visited = {func_name}
        current_names = [n for n in main.calls if n not in visited]

        for _ in range(depth):
            if not current_names or len(ctx.callees) >= MAX_CONTEXT_ENTITIES:
                break

            to_fetch = list(dict.fromkeys(n for n in current_names if n not in visited))
            if not to_fetch:
                break

            visited.update(to_fetch)
            found = self._store.get_by_names(project_id, to_fetch)

            found_by_name: dict[str, CodeEntity] = {}
            for entity in found:
                if entity.name not in found_by_name:
                    found_by_name[entity.name] = entity

            next_names: list[str] = []
            for name in to_fetch:
                if len(ctx.callees) >= MAX_CONTEXT_ENTITIES:
                    break
                entity = found_by_name.get(name)
                if entity:
                    ctx.callees.append(entity)
                    next_names.extend(c for c in entity.calls if c not in visited)

            current_names = next_names

        if len(ctx.callees) >= MAX_CONTEXT_ENTITIES:
            logger.warning(
                "Callee context cap reached (%d entities)", MAX_CONTEXT_ENTITIES
            )

        # Batch expand callers — one query per depth level
        visited_callers = {func_name}
        current_names = [n for n in main.called_by if n not in visited_callers]

        for _ in range(depth):
            if not current_names or len(ctx.callers) >= MAX_CONTEXT_ENTITIES:
                break

            to_fetch = list(
                dict.fromkeys(n for n in current_names if n not in visited_callers)
            )
            if not to_fetch:
                break

            visited_callers.update(to_fetch)
            found = self._store.get_by_names(project_id, to_fetch)

            found_by_name = {}
            for entity in found:
                if entity.name not in found_by_name:
                    found_by_name[entity.name] = entity

            next_names = []
            for name in to_fetch:
                if len(ctx.callers) >= MAX_CONTEXT_ENTITIES:
                    break
                entity = found_by_name.get(name)
                if entity:
                    ctx.callers.append(entity)
                    next_names.extend(
                        c for c in entity.called_by if c not in visited_callers
                    )

            current_names = next_names

        if len(ctx.callers) >= MAX_CONTEXT_ENTITIES:
            logger.warning(
                "Caller context cap reached (%d entities)", MAX_CONTEXT_ENTITIES
            )

        # Same-file entities (already a single query)
        same_file = self._store.get_by_file(project_id, main.file_path)
        ctx.same_file = [e for e in same_file if e.name != func_name]

        return ctx
