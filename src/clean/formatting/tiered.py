"""Tiered response formatter for search_code results.

All tiers return compact summaries (signature + docstring snippet + metadata).
Agents use expand_result(rank=N) to retrieve full source code.

Rank #1 additionally shows call-graph context (CALLS / CALLED BY / SAME FILE).
Ranks #2-5 show summary without call-graph context.
Ranks #6+ show the most compact form: header + file + signature + expand hint.
"""

from __future__ import annotations

from ..core.models import CodeEntity, SearchContext, SearchResult

_SEP_WIDE = (
    "\u2501" * 60
)  # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _normalize_score(similarity: float, max_similarity: float) -> tuple[str, str]:
    """Return (display_pct, label) for normalized similarity.

    Args:
        similarity: Raw cosine similarity for this result.
        max_similarity: Raw cosine similarity of the top result.

    Returns:
        A tuple of (percentage string, qualitative label).
    """
    if max_similarity <= 0:
        return "0%", "Weak"
    normalized = similarity / max_similarity
    pct = f"{int(normalized * 100)}%"
    if normalized >= 0.80:
        label = "Strong"
    elif normalized >= 0.60:
        label = "Good"
    elif normalized >= 0.40:
        label = "Moderate"
    else:
        label = "Weak"
    return pct, label


def _loc_range(entity: CodeEntity) -> str:
    return f"{entity.file_path}:{entity.line_start}-{entity.line_end}"


def _first_n_lines(code: str, n: int) -> list[str]:
    return code.split("\n")[:n]


def _signature_line(code: str) -> str:
    """Return the first non-empty line of code (the declaration/signature)."""
    for line in code.split("\n"):
        stripped = line.strip()
        if stripped:
            return stripped
    return code.split("\n")[0]


def _docstring_block(code: str, max_lines: int = 8) -> list[str]:
    """Extract the docstring/leading comment block from code, up to max_lines.

    Recognises:
    - Triple-quoted Python docstrings (\"\"\" / ''')
    - Single-line # comments at the top
    - JSDoc / block comments (/** ... */ or /* ... */)
    """
    lines = code.split("\n")
    if not lines:
        return []

    result: list[str] = []
    in_triple = False
    in_block = False
    triple_char: str = ""

    for line in lines[1:]:  # skip the declaration line itself
        stripped = line.strip()
        if not in_triple and not in_block:
            # Python triple-quote open
            if stripped.startswith('"""') or stripped.startswith("'''"):
                triple_char = stripped[:3]
                in_triple = True
                result.append(line)
                # Single-line docstring: opens and closes on same line
                rest = stripped[3:]
                if triple_char in rest:
                    in_triple = False
                    break
                continue
            # JSDoc / block comment open
            if stripped.startswith("/**") or stripped.startswith("/*"):
                in_block = True
                result.append(line)
                if "*/" in stripped[2:]:
                    in_block = False
                    break
                continue
            # Single-line # comment
            if stripped.startswith("#"):
                result.append(line)
                continue
            # Single-line // comment
            if stripped.startswith("//"):
                result.append(line)
                continue
            # First non-comment, non-empty line that isn't a docstring — stop
            if stripped:
                break
        elif in_triple:
            result.append(line)
            if triple_char in stripped and stripped != triple_char:
                in_triple = False
                break
            if stripped == triple_char:
                in_triple = False
                break
        elif in_block:
            result.append(line)
            if "*/" in stripped:
                in_block = False
                break

        if len(result) >= max_lines:
            break

    return result


def _format_tier1(
    result: SearchResult,
    context: SearchContext | None,
    max_lines: int,
    max_sim: float,
) -> str:
    """Format the top result as a compact summary with call-graph context.

    Does not emit full source code. Agents call expand_result(rank=1) for that.
    """
    entity = result.entity
    pct, label = _normalize_score(result.similarity, max_sim)
    total_lines = len(entity.code.split("\n")) if entity.code else 0

    header_label = f"#1 BEST MATCH ({label} \u00b7 {pct})"
    sep_prefix = f"\u2501\u2501\u2501 {header_label} "
    sep_fill = "\u2501" * max(0, 60 - len(sep_prefix))

    parts: list[str] = []
    parts.append(sep_prefix + sep_fill)
    parts.append(f"\U0001f4c4 {_loc_range(entity)} ({total_lines} lines)")

    if entity.code:
        sig = _signature_line(entity.code)
        parts.append(f"  {sig}")
        doc_lines = _docstring_block(entity.code, max_lines=4)
        if doc_lines:
            for doc_line in doc_lines:
                parts.append(f"  {doc_line}")

    # Call-graph context
    if context:
        parts.append("")
        if context.callees:
            callee_parts: list[str] = []
            for callee in context.callees:
                callee_parts.append(
                    f"{callee.name} ({callee.file_path}:{callee.line_start})"
                )
            parts.append("CALLS \u2192 " + " | ".join(callee_parts))

        if context.callers:
            caller_parts: list[str] = []
            for caller in context.callers:
                caller_parts.append(
                    f"{caller.name} ({caller.file_path}:{caller.line_start})"
                )
            parts.append("CALLED BY \u2190 " + " | ".join(caller_parts))

        if context.same_file:
            same_file_parts = [
                f"{e.name} (line {e.line_start})" for e in context.same_file
            ]
            parts.append("SAME FILE: " + ", ".join(same_file_parts))

    parts.append("\u2192 expand_result(rank=1) to read full source")

    return "\n".join(parts)


def _format_tier2(
    rank: int, result: SearchResult, max_lines: int, max_sim: float
) -> str:
    """Format results #2-5 as compact summaries without call-graph context."""
    entity = result.entity
    pct, label = _normalize_score(result.similarity, max_sim)
    total_lines = len(entity.code.split("\n")) if entity.code else 0

    sep_label = f"\u2501\u2501\u2501 #{rank} ({label} \u00b7 {pct}) "
    sep_fill = "\u2501" * max(0, 60 - len(sep_label))

    parts: list[str] = []
    parts.append(sep_label + sep_fill)
    parts.append(f"\U0001f4c4 {_loc_range(entity)} ({total_lines} lines)")

    if entity.code:
        sig = _signature_line(entity.code)
        parts.append(f"  {sig}")
        doc_lines = _docstring_block(entity.code, max_lines=4)
        if doc_lines:
            for doc_line in doc_lines:
                parts.append(f"  {doc_line}")

    parts.append(f"\u2192 expand_result(rank={rank}) to read full source")

    return "\n".join(parts)


def _format_tier3(rank: int, result: SearchResult, max_sim: float) -> str:
    """Format results #6+ with signature only — most compact summary form."""
    entity = result.entity
    pct, label = _normalize_score(result.similarity, max_sim)
    total_lines = len(entity.code.split("\n")) if entity.code else 0
    sig = _signature_line(entity.code) if entity.code else entity.name

    sep_label = f"\u2501\u2501\u2501 #{rank} ({label} \u00b7 {pct}) "
    sep_fill = "\u2501" * max(0, 60 - len(sep_label))

    parts: list[str] = []
    parts.append(sep_label + sep_fill)
    parts.append(f"\U0001f4c4 {_loc_range(entity)} ({total_lines} lines)")
    parts.append(f"  {sig}")
    parts.append(f"\u2192 expand_result(rank={rank}) to read full source")

    return "\n".join(parts)


def format_tiered_results(
    results: list[SearchResult],
    context: SearchContext | None,
    max_tier1_lines: int = 500,
    max_tier2_lines: int = 60,
) -> str:
    """Format search results with tiered compact summaries.

    All tiers return a summary (signature + docstring snippet + metadata).
    No tier dumps full source code. Use expand_result(rank=N) to read code.

    Args:
        results: Ordered list of search results (highest similarity first).
        context: Expanded call-graph context for the top result, or None.
        max_tier1_lines: Retained for backward compatibility; no longer used
            to cap source output since full code is never shown inline.
        max_tier2_lines: Retained for backward compatibility; no longer used
            to decide between full vs. partial display.

    Returns:
        A formatted string ready for inclusion in an MCP TextContent response.

    Tier breakdown:
        - Rank #1: Compact summary + call-graph context (CALLS / CALLED BY /
          SAME FILE) + expand_result hint.
        - Ranks #2-5: Compact summary (signature + docstring snippet) +
          expand_result hint. No call-graph context.
        - Ranks #6+: Minimal summary (signature only) + expand_result hint.

    Scores are normalized relative to the top result so agents see meaningful
    relative percentages rather than raw cosine similarities.
    """
    if not results:
        return "No results."

    max_sim = max(r.similarity for r in results) if results else 1.0

    sections: list[str] = []

    for i, result in enumerate(results):
        rank = i + 1
        if rank == 1:
            sections.append(_format_tier1(result, context, max_tier1_lines, max_sim))
        elif rank <= 5:
            sections.append(_format_tier2(rank, result, max_tier2_lines, max_sim))
        else:
            sections.append(_format_tier3(rank, result, max_sim))

    return "\n\n".join(sections)
