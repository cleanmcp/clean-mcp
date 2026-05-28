"""Configuration for Clean. Dataclass-based, no globals."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class ParserConfig:
    extension_languages: dict[str, str] = field(
        default_factory=lambda: {
            ".py": "python",
            ".js": "javascript",
            ".jsx": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".mjs": "javascript",
            ".cjs": "javascript",
        }
    )


@dataclass
class IndexerConfig:
    skip_dirs: set[str] = field(
        default_factory=lambda: {
            "node_modules",
            ".git",
            "__pycache__",
            ".venv",
            "venv",
            ".env",
            "dist",
            "build",
            ".next",
            ".nuxt",
            "coverage",
            ".pytest_cache",
            ".mypy_cache",
            ".tox",
            "egg-info",
            ".eggs",
        }
    )
    batch_size: int = 5000


@dataclass
class EmbedderConfig:
    model_name: str = "all-MiniLM-L6-v2"
    embedding_dimensions: int = 384
    show_progress_bar: bool = True


@dataclass
class StorageConfig:
    default_persist_dir: str = ""  # empty -> ~/.clean/index (set in default_persist_path)
    uri: str = ""  # LanceDB URI; empty = local

    @property
    def default_persist_path(self) -> str:
        if self.default_persist_dir:
            return self.default_persist_dir
        from pathlib import Path
        return str(Path.home() / ".clean" / "index")


@dataclass
class SearchConfig:
    default_top_k: int = 5
    default_depth: int = 1
    max_depth: int = 5


@dataclass
class ToonFormatterConfig:
    columns: list[tuple[str, str, int]] = field(
        default_factory=lambda: [
            ("name", "function_name", 20),
            ("file_path", "file_path", 30),
            ("line_start", "line", 6),
            ("similarity", "similarity", 10),
        ]
    )
    row_indent: str = "  "
    column_separator: str = " | "
    max_code_lines: int = 20
    truncation_indicator: str = "..."


@dataclass
class ApiConfig:
    repos_dir: str = ""  # default: ~/.clean/repos
    db_path: str = ""  # default: ~/.clean/metadata.db


@dataclass
class CleanConfig:
    """Top-level configuration container."""

    parser: ParserConfig = field(default_factory=ParserConfig)
    indexer: IndexerConfig = field(default_factory=IndexerConfig)
    embedder: EmbedderConfig = field(default_factory=EmbedderConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    toon_formatter: ToonFormatterConfig = field(default_factory=ToonFormatterConfig)
    api: ApiConfig = field(default_factory=ApiConfig)
    debug: bool = False
    verbose: bool = False

    @classmethod
    def from_env(cls) -> CleanConfig:
        """Create config with environment variable overrides."""
        config = cls()

        if model := os.getenv("CLEAN_EMBEDDING_MODEL"):
            config.embedder.model_name = model

        if dim := os.getenv("CLEAN_EMBEDDING_DIMENSIONS"):
            try:
                config.embedder.embedding_dimensions = int(dim)
            except ValueError:
                pass

        if persist := os.getenv("CLEAN_PERSIST_PATH"):
            config.storage.default_persist_dir = persist

        if skip_dirs := os.getenv("CLEAN_SKIP_DIRS"):
            config.indexer.skip_dirs = set(d.strip() for d in skip_dirs.split(","))

        if batch := os.getenv("CLEAN_BATCH_SIZE"):
            try:
                config.indexer.batch_size = int(batch)
            except ValueError:
                pass

        if top_k := os.getenv("CLEAN_DEFAULT_TOP_K"):
            try:
                config.search.default_top_k = int(top_k)
            except ValueError:
                pass

        if depth := os.getenv("CLEAN_DEFAULT_DEPTH"):
            try:
                config.search.default_depth = int(depth)
            except ValueError:
                pass

        if debug := os.getenv("CLEAN_DEBUG"):
            config.debug = debug.lower() in ("true", "1", "yes")

        if verbose := os.getenv("CLEAN_VERBOSE"):
            config.verbose = verbose.lower() in ("true", "1", "yes")

        # Storage paths
        if repos_dir := os.getenv("CLEAN_REPOS_DIR"):
            config.api.repos_dir = repos_dir
        if db_path := os.getenv("CLEAN_DB_PATH"):
            config.api.db_path = db_path

        return config


def load_config() -> CleanConfig:
    """Convenience wrapper: create a CleanConfig from environment variables."""
    return CleanConfig.from_env()
