"""Token savings tracking (persisted to disk)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


DEFAULT_STATS_PATH = Path.home() / ".clean" / "stats.json"


@dataclass
class TokenStats:
    total_searches: int = 0
    total_json_chars: int = 0
    total_toon_chars: int = 0
    total_chars_saved: int = 0
    total_json_tokens_est: int = 0
    total_toon_tokens_est: int = 0
    total_tokens_saved_est: int = 0
    avg_savings_percent: float = 0.0
    first_search_at: str | None = None
    last_search_at: str | None = None
    session_searches: int = 0
    session_chars_saved: int = 0
    session_tokens_saved_est: int = 0


class StatsTracker:
    """Tracks and persists token savings statistics."""

    def __init__(self, stats_path: Path | None = None) -> None:
        self._path = stats_path or DEFAULT_STATS_PATH
        self.stats = self._load()

    def _load(self) -> TokenStats:
        if self._path.exists():
            try:
                with open(self._path) as f:
                    data = json.load(f)
                data["session_searches"] = 0
                data["session_chars_saved"] = 0
                data["session_tokens_saved_est"] = 0
                return TokenStats(**data)
            except (json.JSONDecodeError, TypeError):
                pass
        return TokenStats()

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "w") as f:
                json.dump(asdict(self.stats), f, indent=2)
        except Exception:
            pass

    def record_search(self, json_output: str, toon_output: str) -> None:
        s = self.stats
        json_chars = len(json_output)
        toon_chars = len(toon_output)
        saved = json_chars - toon_chars
        json_tokens = json_chars // 4
        toon_tokens = toon_chars // 4

        s.total_searches += 1
        s.total_json_chars += json_chars
        s.total_toon_chars += toon_chars
        s.total_chars_saved += saved
        s.total_json_tokens_est += json_tokens
        s.total_toon_tokens_est += toon_tokens
        s.total_tokens_saved_est += saved // 4

        if s.total_json_chars > 0:
            s.avg_savings_percent = round(
                (s.total_chars_saved / s.total_json_chars) * 100, 2
            )

        now = datetime.now().isoformat()
        if not s.first_search_at:
            s.first_search_at = now
        s.last_search_at = now

        s.session_searches += 1
        s.session_chars_saved += saved
        s.session_tokens_saved_est += saved // 4

        self._save()

    def get_summary(self) -> str:
        s = self.stats
        if s.total_searches == 0:
            return "No searches recorded yet."

        return "\n".join(
            [
                "=" * 55,
                "           Clean Token Savings Statistics",
                "=" * 55,
                "",
                f"  Total Searches:           {s.total_searches:,}",
                "",
                "  Character Savings:",
                f"    JSON output (total):    {s.total_json_chars:,} chars",
                f"    TOON output (total):    {s.total_toon_chars:,} chars",
                f"    Characters saved:       {s.total_chars_saved:,} chars",
                "",
                "  Estimated Token Savings:",
                f"    JSON tokens (est):      {s.total_json_tokens_est:,}",
                f"    TOON tokens (est):      {s.total_toon_tokens_est:,}",
                f"    Tokens saved (est):     {s.total_tokens_saved_est:,}",
                "",
                f"  Average Savings:          {s.avg_savings_percent}%",
                "",
                "  This Session:",
                f"    Searches:               {s.session_searches}",
                f"    Tokens saved:           {s.session_tokens_saved_est:,}",
                "",
                f"  Tracking since:           {s.first_search_at[:10] if s.first_search_at else 'N/A'}",
                f"  Last search:              {s.last_search_at[:19] if s.last_search_at else 'N/A'}",
                "",
                "=" * 55,
            ]
        )

    def reset(self) -> None:
        self.stats = TokenStats()
        self._save()
