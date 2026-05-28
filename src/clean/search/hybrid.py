"""Hybrid search utilities — identifier extraction and result merging."""

from __future__ import annotations

import re

from ..core.models import CodeEntity, SearchResult

# ---------------------------------------------------------------------------
# Identifier detection regexes
# ---------------------------------------------------------------------------

# PascalCase: starts with uppercase, has at least one more uppercase-then-lower
# transition, e.g. ClientLayout, DashboardPage
_RE_PASCAL = re.compile(r"^[A-Z][a-z]+(?:[A-Z][a-zA-Z]*)+$")

# camelCase: starts with lowercase, has at least one uppercase letter inside
_RE_CAMEL = re.compile(r"^[a-z]+[A-Z]")

# Strip leading/trailing punctuation from a word token
_RE_STRIP_PUNCT = re.compile(r"^[^\w]+|[^\w]+$")


def _looks_like_identifier(token: str) -> bool:
    """Return True if *token* matches a recognisable code identifier pattern.

    Args:
        token: A single word token, already stripped of surrounding punctuation.

    Returns:
        True when the token resembles a PascalCase, camelCase, snake_case,
        UPPER_CASE, dotted-path, or file-path identifier.
    """
    if not token:
        return False

    # Dotted path: something.something (dot not at start or end)
    if "." in token and not token.startswith(".") and not token.endswith("."):
        return True

    # File path fragment: contains a forward slash
    if "/" in token:
        return True

    # PascalCase
    if _RE_PASCAL.match(token):
        return True

    # camelCase
    if _RE_CAMEL.match(token):
        return True

    # snake_case: contains underscore and all parts are purely alphabetic/digits
    if "_" in token:
        parts = token.split("_")
        # Require at least 2 parts and each part non-empty
        if len(parts) >= 2 and all(p and p.isalnum() for p in parts):
            # UPPER_CASE: all alpha-parts are uppercase
            alpha_parts = [p for p in parts if p.isalpha()]
            if alpha_parts:
                return True  # covers both snake_case and UPPER_CASE

    return False


def extract_identifiers(query: str) -> list[str]:
    """Extract potential code identifiers from a natural language query.

    Scans each whitespace-separated token in *query*, strips surrounding
    punctuation, and returns those that look like code identifiers
    (PascalCase, camelCase, snake_case, UPPER_CASE, dotted paths, or file
    path fragments).  The returned list is deduplicated while preserving
    first-seen order.

    Args:
        query: The raw search query string.

    Returns:
        A deduplicated list of identifier-like tokens extracted from the query.

    Examples:
        >>> extract_identifiers("find ClientLayout component")
        ['ClientLayout']
        >>> extract_identifiers("where is get_user_by_id defined")
        ['get_user_by_id']
        >>> extract_identifiers("look in src/components/auth")
        ['src/components/auth']
    """
    seen: dict[str, None] = {}  # ordered-set via dict keys
    for raw_token in query.split():
        # Strip surrounding punctuation (quotes, brackets, commas, etc.)
        token = _RE_STRIP_PUNCT.sub("", raw_token)
        if _looks_like_identifier(token):
            seen[token] = None
    return list(seen)


# ---------------------------------------------------------------------------
# Result merging
# ---------------------------------------------------------------------------


def merge_results(
    semantic_results: list[SearchResult],
    name_results: list[CodeEntity],
    path_results: list[CodeEntity],
    semantic_weight: float = 0.6,
    name_weight: float = 0.3,
    path_weight: float = 0.1,
) -> list[SearchResult]:
    """Merge results from semantic search, name lookup, and path lookup.

    Assigns weighted scores to each result source and combines them so that
    entities appearing in multiple sources receive additive scores.  The
    merged list is sorted by final score descending.

    Scoring rules:

    - **Semantic**: ``score = similarity * semantic_weight``
    - **Name match**: ``score = name_weight`` (the store already performs an
      exact/substring match; all returned entities are treated as equally
      relevant at full name_weight)
    - **Path match**: ``score = path_weight``

    When the same entity appears in more than one source its scores are
    summed, which naturally promotes entities that are a strong semantic
    match *and* match by name or path.

    Args:
        semantic_results: Results from vector-similarity search, each carrying
            a ``similarity`` score in [0, 1].
        name_results: Entities returned by a name-substring lookup.
        path_results: Entities returned by a file-path-substring lookup.
        semantic_weight: Weight applied to semantic similarity scores.
        name_weight: Base score assigned to each name-match result.
        path_weight: Base score assigned to each path-match result.

    Returns:
        A list of :class:`~clean.core.models.SearchResult` objects sorted by
        combined score descending.  Each result's ``similarity`` field holds
        the final merged score, rounded to four decimal places.
    """
    # entity_id -> (entity, accumulated_score)
    scores: dict[str, tuple[CodeEntity, float]] = {}

    for result in semantic_results:
        entity = result.entity
        contribution = result.similarity * semantic_weight
        if entity.id in scores:
            _, prev = scores[entity.id]
            scores[entity.id] = (entity, prev + contribution)
        else:
            scores[entity.id] = (entity, contribution)

    for entity in name_results:
        contribution = name_weight
        if entity.id in scores:
            prev_entity, prev = scores[entity.id]
            scores[entity.id] = (prev_entity, prev + contribution)
        else:
            scores[entity.id] = (entity, contribution)

    for entity in path_results:
        contribution = path_weight
        if entity.id in scores:
            prev_entity, prev = scores[entity.id]
            scores[entity.id] = (prev_entity, prev + contribution)
        else:
            scores[entity.id] = (entity, contribution)

    merged = [
        SearchResult(entity=entity, similarity=round(score, 4))
        for entity, score in scores.values()
    ]
    merged.sort(key=lambda r: r.similarity, reverse=True)
    return merged
