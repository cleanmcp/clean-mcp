"""JSON formatter for search results."""

from __future__ import annotations

import json

from ..core.models import SearchResult


class JsonFormatter:
    """Format search results as JSON."""

    def format_results(
        self, results: list[SearchResult], context: dict | None = None
    ) -> str:
        output = []
        for sr in results:
            e = sr.entity
            output.append(
                {
                    "function_name": e.name,
                    "file_path": e.file_path,
                    "line_start": e.line_start,
                    "line_end": e.line_end,
                    "similarity": sr.similarity,
                    "code": e.code,
                    "calls": list(e.calls),
                    "called_by": list(e.called_by),
                    "exported": e.exported,
                }
            )
        return json.dumps(output, indent=2)
