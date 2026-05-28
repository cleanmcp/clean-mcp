"""CodeSearcher — embed query → similarity search → context expansion."""

from __future__ import annotations

import os

from ..core.config import CleanConfig
from ..core.models import CodeEntity
from ..core.protocols import Embedder, VectorStore
from ..util.logging import get_logger
from ..util.security import validate_query
from .context import ContextExpander
from .hybrid import extract_identifiers, merge_results

logger = get_logger(__name__)


class CodeSearcher:
    """Semantic code search with optional context expansion."""

    def __init__(
        self,
        store: VectorStore,
        embedder: Embedder,
        context_expander: ContextExpander,
        config: CleanConfig,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._context = context_expander
        self._config = config

    def search(
        self,
        query: str,
        project_path: str,
        top_k: int | None = None,
        depth: int | None = None,
        project_id: str | None = None,
    ) -> dict:
        """Search for code matching query using hybrid semantic + keyword search.

        Combines vector-similarity search with name-substring and file-path
        lookups when the query contains recognisable code identifiers
        (PascalCase, camelCase, snake_case, UPPER_CASE, dotted paths, or file
        path fragments).  When no identifiers are detected the method falls
        back to pure semantic search so that natural-language queries continue
        to work exactly as before.

        Args:
            query: The search query (natural language or identifier-based).
            project_path: Filesystem path to the project root, used to derive
                *project_id* when *project_id* is not supplied explicitly.
            top_k: Number of results to return.  Defaults to the value in
                ``config.search.default_top_k``.
            depth: Context-expansion depth.  Defaults to
                ``config.search.default_depth``.
            project_id: Override the auto-derived project identifier.

        Returns:
            A dict with two keys:

            - ``results``: ``list[SearchResult]`` — ranked by combined score.
            - ``context``: ``SearchContext | None`` — caller/callee/same-file
              context expanded from the top result, or ``None`` when *depth*
              is 0 or the result list is empty.
        """
        query = validate_query(query)
        top_k = top_k or self._config.search.default_top_k
        depth = depth if depth is not None else self._config.search.default_depth

        # Clamp values
        top_k = max(1, min(top_k, 50))
        depth = max(0, min(depth, self._config.search.max_depth))

        project_id = project_id or self._project_id(project_path)

        # Embed the query
        query_embedding = self._embedder.embed_query(query)

        # --- Semantic search (fetch extra candidates for merging) -------------
        # When we will be merging with keyword results we gather more semantic
        # candidates so the final top-k cut is made from a wider pool.
        identifiers = extract_identifiers(query)
        semantic_fetch = top_k * 2 if identifiers else top_k
        semantic_results = self._store.search(
            project_id, query_embedding, semantic_fetch
        )

        if not identifiers:
            # Pure semantic path — no merging needed, keep existing behaviour.
            results = semantic_results[:top_k]
        else:
            # --- Keyword-based lookups ----------------------------------------
            name_entities: list[CodeEntity] = []
            path_entities: list[CodeEntity] = []

            for identifier in identifiers:
                if "/" in identifier:
                    # Treat as a file-path fragment
                    path_entities.extend(
                        self._store.get_by_file_substring(project_id, identifier)
                    )
                else:
                    name_entities.extend(
                        self._store.get_by_name_substring(project_id, identifier)
                    )

            # --- Merge and cut to top_k ---------------------------------------
            merged = merge_results(
                semantic_results=semantic_results,
                name_results=name_entities,
                path_results=path_entities,
            )
            results = merged[:top_k]

        # --- Context expansion ------------------------------------------------
        context = None
        if depth > 0 and results:
            best = results[0].entity
            context = self._context.expand(project_id, best.name, depth)

        return {
            "results": results,
            "context": context,
        }

    def is_indexed(self, project_path: str, project_id: str | None = None) -> bool:
        """Check if a project has been indexed."""
        project_id = project_id or self._project_id(project_path)
        return self._store.count(project_id) > 0

    @staticmethod
    def _project_id(path: str) -> str:
        return os.path.basename(os.path.abspath(path)).lower().replace(" ", "_")
