"""TOON (Token-Optimized Object Notation) formatter — 30-40% token savings."""

from __future__ import annotations

from ..core.config import ToonFormatterConfig
from ..core.models import SearchResult


def _truncate(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return value[: max_length - 3] + "..."


class ToonFormatter:
    """Format search results in compact TOON tabular format."""

    def __init__(self, config: ToonFormatterConfig | None = None) -> None:
        self._config = config or ToonFormatterConfig()

    def format_results(
        self, results: list[SearchResult], context: dict | None = None
    ) -> str:
        if not results:
            return "results\n  (empty)"

        formatters = {
            "similarity": lambda x: f"{int(x * 100)}%",
            "line_start": str,
        }

        columns = []
        for key, header, max_width in self._config.columns:
            fmt = formatters.get(key, str)
            columns.append((key, header, max_width, fmt))

        rows = []
        for sr in results:
            row = []
            for key, _, max_width, fmt in columns:
                if key == "similarity":
                    value = sr.similarity
                else:
                    value = getattr(sr.entity, key, "")
                formatted = fmt(value) if value != "" else ""
                row.append(_truncate(str(formatted), max_width))
            rows.append(row)

        col_widths = []
        for i, (_, header, _, _) in enumerate(columns):
            data_max = max((len(rows[j][i]) for j in range(len(rows))), default=0)
            col_widths.append(max(len(header), data_max))

        indent = self._config.row_indent
        sep = self._config.column_separator

        lines = ["results"]
        headers = [columns[i][1].ljust(col_widths[i]) for i in range(len(columns))]
        lines.append(indent + sep.join(headers))

        for row in rows:
            cells = [row[i].ljust(col_widths[i]) for i in range(len(columns))]
            lines.append(indent + sep.join(cells))

        return "\n".join(lines)
