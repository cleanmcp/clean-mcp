"""Rich formatter — human-readable with context sections."""

from __future__ import annotations

from ..core.config import ToonFormatterConfig
from ..core.models import SearchContext, SearchResult


class RichFormatter:
    """Format results with full code and relationship context."""

    def __init__(self, config: ToonFormatterConfig | None = None) -> None:
        self._config = config or ToonFormatterConfig()

    def format_results(
        self, results: list[SearchResult], context: dict | None = None
    ) -> str:
        if not results:
            return "No results found."

        lines: list[str] = []
        best = results[0]
        e = best.entity
        pct = int(best.similarity * 100)

        lines.append(
            f"FOUND: {e.name}() in {e.file_path}:{e.line_start} ({pct}% match)"
        )
        lines.append("")

        if e.code:
            lines.append("CODE:")
            code_lines = e.code.strip().split("\n")
            max_lines = self._config.max_code_lines
            if len(code_lines) > max_lines:
                code_lines = code_lines[:max_lines]
                code_lines.append(f"  {self._config.truncation_indicator} (truncated)")
            for cl in code_lines:
                lines.append(f"  {cl}")
            lines.append("")

        # Context from SearchContext
        if context and isinstance(context, SearchContext):
            if context.callees:
                names = [c.name for c in context.callees]
                lines.append(f"CALLS: {', '.join(names)}")
            if context.callers:
                names = [c.name for c in context.callers]
                lines.append(f"CALLED BY: {', '.join(names)}")
            if context.same_file:
                names = [c.name for c in context.same_file]
                lines.append(f"SAME FILE: {', '.join(names)}")
            if context.callees or context.callers or context.same_file:
                lines.append("")

        if len(results) > 1:
            lines.append("---")
            lines.append("Additional matches:")
            for sr in results[1:]:
                r = sr.entity
                pct = int(sr.similarity * 100)
                lines.append(f"  {r.file_path}:{r.line_start} ({pct}%)")

        return "\n".join(lines)
