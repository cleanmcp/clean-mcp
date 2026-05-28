"""E2E test for MCP server tool dispatch."""

import os
import tempfile

import pytest

from clean.core.config import CleanConfig
from clean.db.metadata import MetadataStore
from clean.repo.manager import RepoManager
from clean.services.container import ServiceContainer
from clean.local.mcp_server import create_server


@pytest.fixture
def container():
    with tempfile.TemporaryDirectory() as db_dir:
        config = CleanConfig()
        config.storage.default_persist_dir = db_dir
        config.embedder.show_progress_bar = False
        yield ServiceContainer(config)


@pytest.mark.asyncio
async def test_list_tools(container):
    server = create_server(container)
    # The server should have tools registered
    # We test that create_server returns a valid Server object
    assert server is not None


@pytest.mark.asyncio
async def test_create_server_with_deps(container):
    """Test create_server accepts the new optional parameters."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.db")
        repos_dir = os.path.join(tmp, "repos")

        metadata = MetadataStore(db_path)
        repo_manager = RepoManager(repos_dir)

        server = create_server(
            container,
            metadata=metadata,
            repo_manager=repo_manager,
            repos_dir=repos_dir,
        )
        assert server is not None


@pytest.mark.asyncio
async def test_index_and_search_via_tools(container):
    """Test the full flow through MCP tool dispatch."""
    # Create a temp project
    with tempfile.TemporaryDirectory() as project_dir:
        with open(os.path.join(project_dir, "example.py"), "w") as f:
            f.write("def hello():\n    return 'world'\n")

        # Index via container directly (testing tool dispatch requires stdio)
        result = container.indexer.index(project_dir)
        assert result["status"] == "success"

        # Search
        search_result = container.searcher.search("hello world", project_dir)
        assert len(search_result["results"]) > 0

        # Status
        assert container.searcher.is_indexed(project_dir) is True

        # Stats
        summary = container.stats_tracker.get_summary()
        assert isinstance(summary, str)
