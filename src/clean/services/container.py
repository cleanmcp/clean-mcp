"""ServiceContainer — dependency injection, wires all components."""

from __future__ import annotations

from ..core.config import CleanConfig
from ..embedding.local import SentenceTransformerEmbedder
from ..formatting.json import JsonFormatter
from ..formatting.rich import RichFormatter
from ..formatting.toon import ToonFormatter
from ..indexing.call_graph import CallGraphBuilder
from ..indexing.file_scanner import FileScanner
from ..indexing.incremental import IncrementalIndexer
from ..indexing.indexer import CodebaseIndexer
from ..parsing.registry import ParserRegistry
from ..search.context import ContextExpander
from ..search.searcher import CodeSearcher
from ..services.project_manager import ProjectManager
from ..stats.tracker import StatsTracker
from ..storage.lancedb import LanceDBStore


class ServiceContainer:
    """Single place where all components are wired. No globals."""

    def __init__(self, config: CleanConfig | None = None) -> None:
        self.config = config or CleanConfig.from_env()

        # Core components
        self.embedder = SentenceTransformerEmbedder(self.config.embedder)
        self.store = LanceDBStore(self.config.storage)
        self.parser_registry = ParserRegistry(self.config.parser)

        # Formatters
        self.toon_formatter = ToonFormatter(self.config.toon_formatter)
        self.rich_formatter = RichFormatter(self.config.toon_formatter)
        self.json_formatter = JsonFormatter()

        # Indexing
        self.scanner = FileScanner(self.config)
        self.call_graph = CallGraphBuilder(self.store)
        self.incremental = IncrementalIndexer(self.store)
        self.indexer = CodebaseIndexer(
            store=self.store,
            embedder=self.embedder,
            parser_registry=self.parser_registry,
            scanner=self.scanner,
            call_graph=self.call_graph,
            incremental=self.incremental,
            config=self.config,
        )

        # Search
        self.context_expander = ContextExpander(self.store)
        self.searcher = CodeSearcher(
            store=self.store,
            embedder=self.embedder,
            context_expander=self.context_expander,
            config=self.config,
        )

        # Services
        self.project_manager = ProjectManager(self.store)
        self.stats_tracker = StatsTracker()

    def warmup(self) -> None:
        """Pre-load heavy resources (embedding model). Call at startup."""
        self.embedder.warmup()
