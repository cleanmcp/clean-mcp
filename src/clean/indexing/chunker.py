"""Chunker for large code entities.

Splits entities > CHUNK_THRESHOLD lines into smaller chunks, each with the
parent function's signature prepended for embedding context.
"""

from __future__ import annotations

import hashlib
import re

from ..core.models import CodeEntity

CHUNK_THRESHOLD = 80  # Only chunk entities with more than this many lines
CHUNK_TARGET_MIN = 40  # Minimum lines per chunk
CHUNK_TARGET_MAX = 80  # Maximum lines per chunk

# Patterns that indicate a natural boundary (comment or declaration start)
_BOUNDARY_RE = re.compile(
    r"^\s*(?:#|//|/\*|\*|\"\"\"|\'\'\'"  # comment starters
    r"|(?:function|class|const|let|var|def|async|export|return)\b"  # declaration keywords
    r")",
)


def _is_natural_boundary(line: str) -> bool:
    """Return True if *line* starts a comment or a function/class declaration."""
    return bool(_BOUNDARY_RE.match(line))


def _find_split_point(lines: list[str], search_start: int, search_end: int) -> int:
    """Find the best split point within [search_start, search_end).

    Priority order:
    1. Blank line followed immediately by a comment or declaration.
    2. Double blank line (two consecutive empty lines).
    3. Single blank line.
    4. Fallback: search_end (hard split at CHUNK_TARGET_MAX).

    Args:
        lines: All lines being scanned (relative to the body, not the full file).
        search_start: Inclusive lower bound for the split candidate index.
        search_end: Exclusive upper bound; if nothing is found, return this.

    Returns:
        The index at which to split (the first line of the *next* chunk).
    """
    blank_followed_by_boundary: int | None = None
    double_blank: int | None = None
    single_blank: int | None = None

    i = search_start
    while i < search_end:
        stripped = lines[i].strip()
        if stripped == "":
            # Check double blank
            if i + 1 < search_end and lines[i + 1].strip() == "":
                if double_blank is None:
                    double_blank = i + 2  # split after the second blank line
                i += 2
                continue
            # Single blank — check whether the next non-blank is a boundary
            if i + 1 < search_end and _is_natural_boundary(lines[i + 1]):
                if blank_followed_by_boundary is None:
                    blank_followed_by_boundary = i + 1
            if single_blank is None:
                single_blank = i + 1
        i += 1

    # Return best candidate found
    if blank_followed_by_boundary is not None:
        return blank_followed_by_boundary
    if double_blank is not None:
        return double_blank
    if single_blank is not None:
        return single_blank
    return search_end


def _chunk_id(file_path: str, name: str, line_start: int) -> str:
    """Generate a stable entity ID for a chunk, mirroring CodeEntity.__post_init__."""
    raw = f"{file_path}:{name}:{line_start}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _split_into_chunks(entity: CodeEntity) -> list[CodeEntity]:
    """Split a large entity into a list of chunk entities.

    The first line of *entity.code* is treated as the function signature and
    is prepended to every chunk so each embedding has context about which
    function it belongs to.

    Args:
        entity: The large CodeEntity to split.

    Returns:
        A list of chunk CodeEntity objects (1-indexed chunk_index).
    """
    all_lines = entity.code.split("\n")
    if not all_lines:
        return [entity]

    signature_line = all_lines[0]
    body_lines = all_lines[1:]  # everything after the signature
    total_body = len(body_lines)

    if total_body == 0:
        return [entity]

    # Collect [start, end) ranges within body_lines for each chunk
    ranges: list[tuple[int, int]] = []
    pos = 0

    while pos < total_body:
        # Determine the search window for finding a natural split
        ideal_end = min(pos + CHUNK_TARGET_MAX, total_body)

        if ideal_end >= total_body:
            # Last chunk — take everything remaining
            ranges.append((pos, total_body))
            break

        # Look for a split point in [pos + CHUNK_TARGET_MIN, pos + CHUNK_TARGET_MAX)
        search_start = min(pos + CHUNK_TARGET_MIN, ideal_end)
        split = _find_split_point(body_lines, search_start, ideal_end)

        if split <= pos:
            # Safety guard: never produce an empty chunk
            split = ideal_end

        ranges.append((pos, split))
        pos = split

    # If the body fit into a single range there is nothing to split — return
    # the original entity unchanged so it keeps chunk_index=0 / total_chunks=0.
    if len(ranges) == 1:
        return [entity]

    total = len(ranges)
    chunks: list[CodeEntity] = []

    for idx, (start, end) in enumerate(ranges, start=1):
        chunk_body = "\n".join(body_lines[start:end])
        chunk_code = f"{signature_line}\n{chunk_body}"

        # line_start / line_end are 1-based positions in the original file.
        # The signature occupies entity.line_start; body starts at line_start + 1.
        chunk_line_start = entity.line_start + 1 + start
        chunk_line_end = entity.line_start + end  # inclusive

        chunk_id = _chunk_id(entity.file_path, entity.name, chunk_line_start)

        chunk = CodeEntity(
            id=chunk_id,
            name=entity.name,
            file_path=entity.file_path,
            code=chunk_code,
            line_start=chunk_line_start,
            line_end=chunk_line_end,
            language=entity.language,
            kind=entity.kind,
            calls=entity.calls,
            called_by=(),
            class_name=entity.class_name,
            exported=entity.exported,
            sub_kind=entity.sub_kind,
            decorators=entity.decorators,
            chunk_index=idx,
            parent_id=entity.id,
            total_chunks=total,
        )
        chunks.append(chunk)

    return chunks


def chunk_entities(entities: list[CodeEntity]) -> list[CodeEntity]:
    """Split large entities into chunks while keeping small ones intact.

    Entities with more than CHUNK_THRESHOLD lines are replaced by their
    constituent chunks.  Smaller entities pass through unchanged with
    chunk_index=0 and total_chunks=0 (their default values).

    Args:
        entities: Input list of parsed CodeEntity objects.

    Returns:
        A new list where large entities are replaced by their chunks.
        The relative order of non-chunked entities is preserved; chunks
        appear in order (1..N) immediately where their parent entity was.
    """
    result: list[CodeEntity] = []

    for entity in entities:
        line_count = len(entity.code.split("\n"))
        if line_count <= CHUNK_THRESHOLD:
            result.append(entity)
        else:
            chunks = _split_into_chunks(entity)
            result.extend(chunks)

    return result
