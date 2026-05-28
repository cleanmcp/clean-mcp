"""Bidirectional call graph builder."""

from __future__ import annotations

from collections import defaultdict
from typing import Sequence

from ..core.models import CodeEntity
from ..core.protocols import VectorStore
from ..util.logging import get_logger

logger = get_logger(__name__)


class CallGraphBuilder:
    """Compute reverse call relationships (called_by) for all entities."""

    def __init__(self, store: VectorStore) -> None:
        self._store = store

    def compute(
        self, project_id: str, entities: Sequence[CodeEntity]
    ) -> list[CodeEntity]:
        """
        Compute called_by for all entities and return updated copies.

        For each entity, finds all other entities that call it by name.
        """
        if not entities:
            return []

        # Build reverse mapping: callee_name -> list of caller names
        called_by_map: dict[str, list[str]] = defaultdict(list)
        for entity in entities:
            for callee_name in entity.calls:
                called_by_map[callee_name].append(entity.name)

        # Update entities with called_by information
        updated: list[CodeEntity] = []
        for entity in entities:
            callers = tuple(called_by_map.get(entity.name, ()))
            if callers != entity.called_by:
                updated.append(entity.with_called_by(callers))
            else:
                updated.append(entity)

        return updated
