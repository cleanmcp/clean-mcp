"""MCP server — repo-name-based tools for enterprise use.

Tools work with owner/repo names instead of local paths. Results use relative
paths so remote clients never see server-side directory structure.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from ..core.errors import RepoError
from ..db.metadata import MetadataStore
from ..db.models import ProjectRecord
from ..formatting.tiered import format_tiered_results
from ..indexing.indexer import detect_repo_metadata
from ..indexing.staleness import check_staleness
from ..mcp.shared import (
    _make_project_id,
    _relativize_context,
    _relativize_results,
    _resolve_single_repo,
    _validate_repo_format,
)
from ..repo.manager import RepoManager
from ..services.container import ServiceContainer
from ..util.file_tree import build_file_tree
from ..util.logging import get_logger
from ..util.source_reader import SourceReaderError, read_source

logger = get_logger(__name__)

# Default timeout for indexing (10 minutes)
DEFAULT_INDEX_TIMEOUT = 600.0


def _detect_git_branch(cwd: str) -> str | None:
    """Return the current git branch name for the repo at *cwd*, or None."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            branch = result.stdout.strip()
            # "HEAD" means detached — not useful
            return branch if branch and branch != "HEAD" else None
    except Exception:
        pass
    return None


def _detect_git_repo(cwd: str) -> str | None:
    """Return 'owner/repo' by parsing the git origin remote URL, or None."""
    import re
    import subprocess

    try:
        result = subprocess.run(
            ["git", "-C", cwd, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            # handles both https://github.com/owner/repo.git and git@github.com:owner/repo.git
            m = re.search(r"[:/]([a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+?)(?:\.git)?$", url)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None


# --- Server factory ---


def _build_repo_hint(metadata: MetadataStore, default_repo: str | None) -> str:
    """Build a dynamic hint string for tool descriptions listing available repos."""
    parts = []
    if default_repo:
        parts.append(f"Default repo: '{default_repo}'.")
    try:
        projects = metadata.list_projects()
        ready = [p for p in projects if p.status == "ready"]
        if ready:
            names = ", ".join(
                f"'{p.repo_full_name}@{p.branch}'"
                if p.branch
                else f"'{p.repo_full_name}'"
                for p in ready
            )
            parts.append(f"Indexed repos: {names}.")
    except Exception:
        pass
    return " ".join(parts)


def create_server(
    container: ServiceContainer,
    metadata: MetadataStore | None = None,
    repo_manager: RepoManager | None = None,
    repos_dir: str | None = None,
    default_repo: str | None = None,
) -> Server:
    """Create and configure the MCP server with repo-name-based tools.

    When metadata/repo_manager are None (standalone CLI mode), they are
    initialized from defaults.

    default_repo: pin a repo (owner/repo) so callers don't need to specify it.
    """
    # Standalone CLI defaults
    if repos_dir is None:
        repos_dir = str(Path.home() / ".clean" / "repos")
    if metadata is None:
        db_path = str(Path.home() / ".clean" / "metadata.db")
        metadata = MetadataStore(db_path)
    if repo_manager is None:
        repo_manager = RepoManager(repos_dir)

    server = Server("clean")
    _search_cache: dict = {}

    @server.list_tools()
    async def list_tools():
        hint = _build_repo_hint(metadata, default_repo)
        repo_desc = "Repository in owner/repo format. OPTIONAL — omit to auto-select."
        if hint:
            repo_desc += f" {hint}"

        # Build search_code description — this is the core product prompt
        search_desc = (
            "Codebase-wide semantic search. Understands what code DOES, not just "
            "what it's named. Returns complete source code, file paths, line numbers, "
            "call graph (callers + callees), and neighbouring functions.\n\n"
            "THIS TOOL REPLACES grep, find, glob, and manual file reading for "
            "understanding code. One call returns exactly the functions you need "
            "with full context — no scanning dozens of files. Use it.\n\n"
            "ALWAYS CALL THIS TOOL:\n"
            "• BEFORE reading any source files — search first, read only if needed\n"
            "• BEFORE using grep/glob/find — semantic search finds what text matching misses\n"
            "• When asked about how ANY feature, module, or system works\n"
            "• When looking for where something is implemented or defined\n"
            "• When debugging — search for the error path or broken behaviour\n"
            "• When planning changes — find all related code before editing\n"
            "• When you need to understand data flow, call chains, or architecture\n\n"
            "DO NOT skip this tool and grep/read files instead. That approach is slower, "
            "misses semantic connections, and wastes context window tokens. This tool "
            "exists specifically to prevent aimless file exploration.\n\n"
            "QUERY TIPS: Describe the BEHAVIOUR you're looking for.\n"
            "Good: 'function that validates email format before signup'\n"
            "Good: 'middleware that checks authentication on API routes'\n"
            "Bad: 'validateEmail' — that's a name, not behaviour\n"
            "Use top_k=10-15 for broad exploration, top_k=3 for targeted lookup."
        )
        if hint:
            search_desc += f"\n\n{hint}"

        tools = []

        # Only expose index_repo when not disabled (e.g. during benchmarks)
        if not os.environ.get("CLEAN_DISABLE_INDEX_TOOL"):
            tools.append(
                Tool(
                    name="index_repo",
                    description=(
                        "Index a repository for semantic search. Two modes:\n"
                        "• LOCAL: pass `path` to index a folder already on disk (no clone, fully offline).\n"
                        "• GITHUB: pass `repo` in owner/repo format to clone from GitHub and index.\n\n"
                        "Parses every function/class/method, generates semantic embeddings, and builds "
                        "a call graph. Safe to re-run — already-indexed repos are detected. "
                        "Indexing takes 1-5 minutes depending on size."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": (
                                    "Absolute path to a local directory to index. "
                                    "The folder's basename becomes the searchable repo name "
                                    "(or 'owner/repo' if the folder is a git repo with a GitHub remote). "
                                    "Use this OR 'repo', not both."
                                ),
                            },
                            "repo": {
                                "type": "string",
                                "description": "Repository in owner/repo format (e.g. 'facebook/react'). Clones from GitHub. Use this OR 'path'.",
                            },
                            "branch": {
                                "type": "string",
                                "description": "Git branch to index (e.g. 'main', 'dev'). Omit to use the repo's default branch.",
                            },
                            "timeout": {
                                "type": "number",
                                "description": "Timeout in seconds. Default: 600 (10 min). Large repos may need more.",
                                "default": 600,
                            },
                            "force": {
                                "type": "boolean",
                                "description": (
                                    "Force a full re-index even if already indexed. "
                                    "Use when the index is corrupted or you need a clean rebuild. "
                                    "Default: false."
                                ),
                                "default": False,
                            },
                        },
                        "required": [],
                    },
                )
            )
            tools.append(
                Tool(
                    name="delete_repo",
                    description=(
                        "Remove an indexed repository — deletes the metadata record, drops the "
                        "LanceDB vector table, and optionally removes the cloned files from disk.\n\n"
                        "Use this to free disk space or to cleanly remove a repo before re-indexing "
                        "with a different branch. After deletion, index_repo must be run again before "
                        "searching."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "repo": {
                                "type": "string",
                                "description": "Repository in owner/repo format (e.g. 'facebook/react')",
                            },
                            "branch": {
                                "type": "string",
                                "description": "Branch to delete. Omit to delete the default branch index.",
                            },
                            "remove_files": {
                                "type": "boolean",
                                "description": (
                                    "Also delete the cloned source files from disk. "
                                    "Default: true."
                                ),
                                "default": True,
                            },
                        },
                        "required": ["repo"],
                    },
                )
            )

        tools.append(
            Tool(
                name="search_code",
                description=search_desc,
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Natural language description of what you're looking for. Be specific about behavior, not names. "
                            "Good: 'function that validates email format before signup'. "
                            "Bad: 'validateEmail'. "
                            "Good: 'error handling in payment processing'. "
                            "Bad: 'try catch payment'.",
                        },
                        "repo": {
                            "type": "string",
                            "description": repo_desc,
                        },
                        "branch": {
                            "type": "string",
                            "description": "Git branch to search (e.g. 'vla', 'dev'). Only needed if you indexed multiple branches of the same repo.",
                        },
                        "cwd": {
                            "type": "string",
                            "description": "Absolute path to the user's current working directory. When provided, the server runs 'git branch' there to auto-detect which branch to search. Pass this on every search so the right branch index is used automatically.",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Number of results to return. Default 5. Use 10-15 for broad exploration, 3 for targeted lookup.",
                            "default": 5,
                        },
                        "depth": {
                            "type": "integer",
                            "description": "How far to expand context around the top result. 0=just matches, 1=direct callers/callees (default), 2=two levels of relationships.",
                            "default": 1,
                        },
                    },
                    "required": ["query"],
                },
            )
        )
        tools.append(
            Tool(
                name="list_repos",
                description=(
                    "List all indexed repositories available for search. Shows repo "
                    "name, branch, status (ready/indexing/error), entity count, and "
                    "detected language/framework.\n\n"
                    "Call this FIRST if you are unsure which repos are indexed. "
                    "The output tells you exactly what you can search across."
                ),
                inputSchema={"type": "object", "properties": {}},
            )
        )
        tools.append(
            Tool(
                name="get_token_savings",
                description="Show cumulative token savings from using compact TOON format vs raw JSON across all searches in this session.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "reset": {
                            "type": "boolean",
                            "description": "Reset all statistics",
                            "default": False,
                        },
                    },
                },
            )
        )
        tools.append(
            Tool(
                name="get_file_tree",
                description=(
                    "Get the directory structure of an indexed repository. Use this BEFORE "
                    "search_code to understand project layout — see route structures (Next.js "
                    "app/ directory), locate component files, and discover where code lives.\n\n"
                    "CALL THIS TOOL:\n"
                    "• As your FIRST step when exploring an unfamiliar codebase\n"
                    "• When you need to understand how files are organized\n"
                    "• When search_code results reference paths you don't recognize\n"
                    "• When you need to find configuration files, test directories, or assets\n\n"
                    "Returns an indented tree view of all code-relevant files and directories."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "repo": {
                            "type": "string",
                            "description": "Repository in owner/repo format (e.g. 'facebook/react')",
                        },
                        "branch": {
                            "type": "string",
                            "description": "Git branch to inspect. Omit to use the repo's default branch.",
                        },
                        "depth": {
                            "type": "integer",
                            "description": "Maximum directory depth to display. Default: 4.",
                            "default": 4,
                        },
                        "include_hidden": {
                            "type": "boolean",
                            "description": "Include hidden directories (those starting with '.'). Default: false.",
                            "default": False,
                        },
                    },
                    "required": [],
                },
            )
        )
        tools.append(
            Tool(
                name="get_source",
                description=(
                    "Read source code directly from an indexed repository. Returns file "
                    "contents with line numbers.\n\n"
                    "USE THIS TOOL:\n"
                    "• After search_code — to get full code for a function that was truncated\n"
                    "• After get_file_tree — to read a specific file you identified\n"
                    "• When you need exact line ranges from search results\n"
                    "• To read configuration files, package.json, or non-code files\n\n"
                    "DO NOT use this as your first step — call search_code or get_file_tree "
                    "first to identify which files matter, then use this for targeted reads.\n\n"
                    "Line numbers are 1-indexed and inclusive. Max 500 lines per call.\n"
                    "For larger files, specify start_line and end_line to read specific sections."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "repo": {
                            "type": "string",
                            "description": "Repository in owner/repo format (e.g. 'facebook/react')",
                        },
                        "file": {
                            "type": "string",
                            "description": "Relative file path within the repository (e.g. 'src/auth/login.py')",
                        },
                        "branch": {
                            "type": "string",
                            "description": "Git branch to read from. Omit to use the repo's default branch.",
                        },
                        "start_line": {
                            "type": "integer",
                            "description": "First line to return (1-indexed, inclusive). Omit to start from line 1.",
                        },
                        "end_line": {
                            "type": "integer",
                            "description": "Last line to return (1-indexed, inclusive). Omit to read up to 500 lines from start_line.",
                        },
                        "function": {
                            "type": "string",
                            "description": (
                                "Function or class name to read. Looks up the entity in the index "
                                "and reads its exact line range. Overrides start_line/end_line. "
                                "Example: function='TokenSavingsCard'"
                            ),
                        },
                    },
                    "required": ["file"],
                },
            )
        )
        tools.append(
            Tool(
                name="expand_result",
                description=(
                    "Get the full source code for a search result by rank number. "
                    "Use this after search_code returns truncated results.\n\n"
                    "Example: search_code returns result #3 with '234 more lines'. "
                    "Call expand_result(rank=3) to get the complete source code.\n\n"
                    "Only works after a search_code call in the same session."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "rank": {
                            "type": "integer",
                            "description": "Result rank number from the last search (1-based, e.g. 1 for the top result, 3 for the third result)",
                        },
                    },
                    "required": ["rank"],
                },
            )
        )
        return tools

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        try:
            if name == "index_repo":
                return await _handle_index_repo(
                    arguments, container, metadata, repo_manager
                )
            elif name == "search_code":
                return await _handle_search_code(
                    arguments,
                    container,
                    metadata,
                    default_repo=default_repo,
                    search_cache=_search_cache,
                )
            elif name == "list_repos":
                return await _handle_list_repos(metadata)
            elif name == "get_token_savings":
                return _handle_get_token_savings(arguments, container)
            elif name == "get_file_tree":
                return await _handle_get_file_tree(arguments, metadata, repo_manager)
            elif name == "get_source":
                return await _handle_get_source(
                    arguments, metadata, repo_manager, container=container
                )
            elif name == "expand_result":
                return await _handle_expand_result(arguments, _search_cache)
            elif name == "delete_repo":
                return await _handle_delete_repo(
                    arguments, container, metadata, repo_manager
                )
            else:
                return [TextContent(type="text", text=f"Unknown tool: {name}")]
        except asyncio.TimeoutError:
            return [TextContent(type="text", text="Error: Operation timed out")]
        except Exception as e:
            logger.exception("Error in tool %s", name)
            return [TextContent(type="text", text=f"Error: {e}")]

    return server


# --- Tool handlers ---


async def _handle_index_local_path(arguments, container, metadata):
    """Index a folder already on disk — no clone, no network."""
    import re as _re

    path_arg = arguments.get("path", "").strip()
    abs_path = os.path.abspath(os.path.expanduser(path_arg))

    if not os.path.isdir(abs_path):
        return [
            TextContent(
                type="text",
                text=f"Error: path does not exist or is not a directory: {abs_path}",
            )
        ]

    # Derive a repo identifier:
    # 1. If it's a git repo with a GitHub remote, use 'owner/repo'.
    # 2. Otherwise use the folder basename (prefixed 'local/' to avoid clashes).
    repo_full_name = _detect_git_repo(abs_path)
    if not repo_full_name:
        folder = os.path.basename(abs_path.rstrip(os.sep)) or "root"
        # Sanitize for project-id safety
        folder = _re.sub(r"[^a-zA-Z0-9._-]", "-", folder).lower() or "local"
        repo_full_name = f"local/{folder}"

    branch = arguments.get("branch", "").strip() or _detect_git_branch(abs_path)
    branch = branch or None

    force = bool(arguments.get("force", False))
    timeout = arguments.get("timeout", DEFAULT_INDEX_TIMEOUT)
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        timeout = DEFAULT_INDEX_TIMEOUT
    timeout = min(timeout, 3600)

    project_id = _make_project_id(repo_full_name, branch)

    existing = metadata.get_project(project_id)
    if existing and not force:
        if existing.status == "ready":
            return [
                TextContent(
                    type="text",
                    text=f"Already indexed: {repo_full_name} ({existing.entity_count} entities) at {existing.local_path}",
                )
            ]
        if existing.status in ("indexing", "cloning"):
            return [
                TextContent(
                    type="text",
                    text=f"Already in progress: {repo_full_name} (status: {existing.status})",
                )
            ]

    if existing and force:
        try:
            container.store.clear(project_id)
        except Exception:
            logger.warning(
                "Failed to clear LanceDB table for %s during force re-index",
                project_id,
                exc_info=True,
            )
        metadata.delete_project(project_id)

    now = datetime.now(timezone.utc).isoformat()
    metadata.save_project(
        ProjectRecord(
            project_id=project_id,
            repo_full_name=repo_full_name,
            branch=branch,
            local_path=abs_path,
            status="indexing",
            created_at=now,
            org_id=None,
        )
    )

    try:
        loop = asyncio.get_running_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(
                None, lambda: container.indexer.index(abs_path, project_id=project_id)
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        metadata.update_project_status(
            project_id, "error", error_message="Indexing timed out"
        )
        return [TextContent(type="text", text=f"Error: Indexing {abs_path} timed out")]
    except Exception as e:
        metadata.update_project_status(project_id, "error", error_message=str(e))
        return [TextContent(type="text", text=f"Error indexing {abs_path}: {e}")]

    if result["status"] == "success":
        entity_count = result["functions_indexed"]
        metadata.update_project_status(project_id, "ready", entity_count=entity_count)
        try:
            all_files: list[str] = []
            for root, _dirs, files in os.walk(abs_path):
                for fname in files:
                    all_files.append(os.path.join(root, fname))
            meta = detect_repo_metadata(abs_path, all_files)
            metadata.update_project_metadata(
                project_id,
                description=meta["description"],
                primary_language=meta["primary_language"],
                tags=meta["tags"],
            )
        except Exception:
            logger.warning(
                "Failed to detect/save repo metadata for %s", project_id, exc_info=True
            )
        inc = " (incremental)" if result.get("incremental") else ""
        text = (
            f"Indexed {repo_full_name}: {entity_count} functions from "
            f"{result['files_processed']} files{inc}\n"
            f"Source: {abs_path}\n"
            f"Search with: search_code query='...' repo='{repo_full_name}'"
        )
    else:
        error_msg = result.get("error", "Unknown")
        metadata.update_project_status(project_id, "error", error_message=error_msg)
        text = f"Error indexing {abs_path}: {error_msg}"

    return [TextContent(type="text", text=text)]


async def _handle_index_repo(
    arguments,
    container,
    metadata,
    repo_manager,
):
    # Local-path mode: index a folder already on disk, no clone.
    path_arg = arguments.get("path", "").strip()
    if path_arg:
        return await _handle_index_local_path(arguments, container, metadata)

    repo = arguments.get("repo", "").strip()
    if not repo:
        return [TextContent(type="text", text="Error: must provide either 'path' (local folder) or 'repo' (owner/repo)")]

    err = _validate_repo_format(repo)
    if err:
        return [TextContent(type="text", text=f"Error: {err}")]

    branch = arguments.get("branch", "").strip() or None

    timeout = arguments.get("timeout", DEFAULT_INDEX_TIMEOUT)
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        timeout = DEFAULT_INDEX_TIMEOUT
    timeout = min(timeout, 3600)

    force = bool(arguments.get("force", False))
    project_id = _make_project_id(repo, branch)

    # Check if already indexed or in progress (exact match)
    existing = metadata.get_project(project_id)
    if existing and not force:
        if existing.status == "ready":
            return [
                TextContent(
                    type="text",
                    text=f"Already indexed: {repo} ({existing.entity_count} entities)",
                )
            ]
        if existing.status in ("indexing", "cloning"):
            return [
                TextContent(
                    type="text",
                    text=f"Already in progress: {repo} (status: {existing.status})",
                )
            ]

    # When force=True, wipe the existing index so we start clean.
    if existing and force:
        logger.info("force re-index requested for %s — clearing existing index", repo)
        try:
            container.store.clear(project_id)
        except Exception:
            logger.warning(
                "Failed to clear LanceDB table for %s during force re-index",
                project_id,
                exc_info=True,
            )
        metadata.delete_project(project_id)
        existing = None

    # Check if the same repo name is indexed under a different owner (only when no branch specified and not force)
    if not force and not branch:
        parts = repo.split("/")
        if len(parts) == 2:
            repo_name = parts[1]
            matches = metadata.find_projects_by_repo_name(repo_name)
            ready_matches = [m for m in matches if m.status == "ready" and not m.branch]
            if ready_matches:
                match_info = ", ".join(
                    f"{m.repo_full_name} ({m.entity_count} entities)"
                    for m in ready_matches
                )
                return [
                    TextContent(
                        type="text",
                        text=f"Already indexed as {match_info}. Use that name for search.",
                    )
                ]

    # Clone the repo (public GitHub URL only — no auth)
    try:
        clone_url = f"https://github.com/{repo}.git"

        # When force=True, delete existing clone so we perform a fresh full clone.
        loop = asyncio.get_running_loop()
        if force and repo_manager.exists(repo, branch):
            try:
                await loop.run_in_executor(
                    None, lambda: repo_manager.delete(repo, branch)
                )
            except Exception:
                logger.warning(
                    "Failed to delete existing clone for %s during force re-index",
                    repo,
                    exc_info=True,
                )

        local_path = await loop.run_in_executor(
            None, lambda: repo_manager.clone(clone_url, repo, branch=branch)
        )
    except RepoError as e:
        return [
            TextContent(
                type="text",
                text=f"Error cloning {repo}: {e}. Is the repository public and does it exist?",
            )
        ]
    except Exception as e:
        # Catch all other clone failures (PermissionError, OSError, subprocess.TimeoutExpired, etc.)
        # and ensure the project is not left stuck in a non-error state.
        project_id_for_error = _make_project_id(repo, branch)
        try:
            metadata.update_project_status(
                project_id_for_error, "error", error_message=str(e)
            )
        except Exception:
            pass
        return [
            TextContent(
                type="text",
                text=f"Error cloning {repo}: {e}",
            )
        ]

    # Create/update project record
    now = datetime.now(timezone.utc).isoformat()
    metadata.save_project(
        ProjectRecord(
            project_id=project_id,
            repo_full_name=repo,
            branch=branch,
            local_path=local_path,
            status="indexing",
            created_at=now,
            org_id=None,
        )
    )

    # Index
    try:
        loop = asyncio.get_running_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(
                None, lambda: container.indexer.index(local_path, project_id=project_id)
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        metadata.update_project_status(
            project_id, "error", error_message="Indexing timed out"
        )
        return [TextContent(type="text", text=f"Error: Indexing {repo} timed out")]
    except Exception as e:
        metadata.update_project_status(project_id, "error", error_message=str(e))
        return [TextContent(type="text", text=f"Error indexing {repo}: {e}")]

    if result["status"] == "success":
        entity_count = result["functions_indexed"]
        metadata.update_project_status(project_id, "ready", entity_count=entity_count)
        # Detect and store repo metadata (language, tags, description)
        try:
            import os as _os

            all_files: list[str] = []
            for root, _dirs, files in _os.walk(local_path):
                for fname in files:
                    all_files.append(_os.path.join(root, fname))
            meta = detect_repo_metadata(local_path, all_files)
            metadata.update_project_metadata(
                project_id,
                description=meta["description"],
                primary_language=meta["primary_language"],
                tags=meta["tags"],
            )
        except Exception:
            logger.warning(
                "Failed to detect/save repo metadata for %s", project_id, exc_info=True
            )
        inc = " (incremental)" if result.get("incremental") else ""
        text = f"Indexed {repo}: {entity_count} functions from {result['files_processed']} files{inc}"
    else:
        error_msg = result.get("error", "Unknown")
        metadata.update_project_status(project_id, "error", error_message=error_msg)
        text = f"Error indexing {repo}: {error_msg}"

    return [TextContent(type="text", text=text)]


async def _handle_search_code(
    arguments,
    container,
    metadata,
    default_repo=None,
    search_cache: dict | None = None,
):
    t0 = time.monotonic()
    org_id = None

    query = arguments.get("query", "").strip()
    if not query:
        return [TextContent(type="text", text="Error: query is required")]

    repo = arguments.get("repo", "").strip()
    branch = arguments.get("branch", "").strip() or None
    cwd = arguments.get("cwd", "").strip() or None

    # Auto-detect repo and branch from the user's working directory
    if cwd:
        if branch is None:
            branch = _detect_git_branch(cwd)
        if not repo:
            # Try git remote first (for cloned repos with a GitHub origin)
            repo = _detect_git_repo(cwd) or ""
            # Fall back to matching cwd against a locally-indexed project's path
            if not repo:
                try:
                    cwd_abs = os.path.abspath(cwd)
                    for p in metadata.list_projects(org_id=None):
                        if p.local_path and os.path.abspath(p.local_path) == cwd_abs:
                            repo = p.repo_full_name
                            if branch is None:
                                branch = p.branch
                            break
                except Exception:
                    pass

    # Validate branch name format when explicitly provided
    import re as _re

    _BRANCH_RE = _re.compile(r"^[a-zA-Z0-9._\-/]+$")
    if branch and not _BRANCH_RE.match(branch):
        return [
            TextContent(
                type="text",
                text=f"Error: Invalid branch name '{branch}'. Branch names may only contain alphanumeric characters, dots, dashes, underscores, and slashes.",
            )
        ]

    # --- Auto-resolve repo (Option A + D) ---
    if not repo:
        if default_repo:
            # Option A: use the configured default
            repo = default_repo
            # Inherit branch from the indexed project if not explicitly specified
            if branch is None:
                default_project = metadata.resolve_project(default_repo, org_id=org_id)
                if default_project:
                    branch = default_project.branch
        else:
            # Option D: auto-select from indexed repos
            resolved = _resolve_single_repo(metadata, org_id=org_id, branch=branch)
            if isinstance(resolved, str):
                return [TextContent(type="text", text=resolved)]
            repo, branch = resolved

    # Reject clearly invalid repo formats (e.g. path traversal) before the DB lookup
    if "/" in repo:
        format_err = _validate_repo_format(repo)
        if format_err:
            return [TextContent(type="text", text=f"Error: {format_err}")]

    # Fuzzy-resolve: handles bare names ("lokus"), owner mismatches, exact matches
    project = metadata.resolve_project(repo, org_id=org_id, branch=branch)
    if not project:
        return [
            TextContent(
                type="text",
                text=f"Repository not indexed: {repo}. Run index_repo first.",
            )
        ]

    if project.status != "ready":
        return [
            TextContent(
                type="text",
                text=f"Repository not ready: {project.repo_full_name} (status: {project.status}). Wait for indexing to complete.",
            )
        ]

    # Auto-reindex if stale — fire-and-forget so search returns immediately on stale data.
    loop = asyncio.get_running_loop()
    try:
        is_stale = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: check_staleness(
                    project.local_path, container.store, project_id=project.project_id
                ),
            ),
            timeout=10.0,
        )
        if is_stale:
            logger.info("Stale index detected for %s, firing background re-index", repo)
            # Fire-and-forget: do not await — search stale data immediately.
            asyncio.ensure_future(
                loop.run_in_executor(
                    None,
                    lambda: container.indexer.index(
                        project.local_path, project_id=project.project_id
                    ),
                )
            )
    except asyncio.TimeoutError:
        logger.warning(
            "Staleness check timed out for %s, proceeding with existing index", repo
        )
    except Exception:
        logger.warning(
            "Staleness check failed for %s, proceeding with existing index",
            repo,
            exc_info=True,
        )

    depth = arguments.get("depth", 1)
    if not isinstance(depth, int) or depth < 0:
        depth = 1
    depth = min(depth, 3)

    top_k_raw = arguments.get("top_k", 5)
    if not isinstance(top_k_raw, int) or top_k_raw < 1:
        top_k_raw = 5
    top_k_clamped = top_k_raw > 50
    top_k = min(top_k_raw, 50)

    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: container.searcher.search(
                    query,
                    project.local_path,
                    top_k=top_k,
                    depth=depth,
                    project_id=project.project_id,
                ),
            ),
            timeout=30.0,
        )
    except asyncio.TimeoutError:
        logger.error(
            "search timed out for repo=%s query=%r", project.repo_full_name, query
        )
        return [
            TextContent(
                type="text",
                text="Error: search timed out after 30 seconds. Try a simpler query.",
            )
        ]

    results = result["results"]
    context = result["context"]

    if not results:
        return [TextContent(type="text", text="No results.")]

    # Relativize paths before formatting
    results = _relativize_results(results, project.local_path)
    context = _relativize_context(context, project.local_path)

    if search_cache is not None:
        # Clear stale cache only after we have fresh results to replace it,
        # so that a failed search does not wipe the previous valid cache.
        search_cache.clear()
        search_cache["repo"] = project.repo_full_name
        search_cache["branch"] = getattr(project, "branch", None)
        search_cache["local_path"] = project.local_path
        search_cache["results"] = results

    # Format with tiered detail: rank #1 gets full code + context, ranks #2-5 partial, #6+ signature only
    text = format_tiered_results(results, context)
    json_text = ""
    try:
        json_text = container.json_formatter.format_results(results)
        container.stats_tracker.record_search(json_text, text)
    except Exception:
        pass

    top_k_note = ""
    if top_k_clamped:
        top_k_note = f"\nNote: top_k was clamped to 50 (requested {top_k_raw}).\n"

    # Add footer to guide efficient follow-up
    footer = (
        "\n\n---\n"
        f"{top_k_note}"
        "Rank #1 contains full source code. Ranks #2-5 show partial code; ranks #6+ show signatures only.\n"
        "Use expand_result(rank=N) to read any truncated result in full.\n"
        "Only call search_code again for a genuinely different concept."
    )
    return [TextContent(type="text", text=text + footer)]


async def _handle_list_repos(metadata):
    import json as _json

    projects = metadata.list_projects(org_id=None)
    if not projects:
        return [
            TextContent(
                type="text",
                text="No repositories indexed yet. Use index_repo to add one.",
            )
        ]

    lines = ["Indexed repositories:"]
    for p in projects:
        count = f"{p.entity_count:,}" if p.entity_count else "0"
        repo_label = f"{p.repo_full_name}@{p.branch}" if p.branch else p.repo_full_name
        # Build optional metadata suffix for Claude's benefit
        meta_parts: list[str] = []
        if p.primary_language:
            meta_parts.append(p.primary_language)
        if p.tags:
            try:
                tag_list: list[str] = _json.loads(p.tags)
                # Show framework tags (skip the language tag already shown)
                extra = [t for t in tag_list if t != p.primary_language]
                if extra:
                    meta_parts.append(", ".join(extra))
            except Exception:
                pass
        if p.description:
            meta_parts.append(f'"{p.description}"')
        meta_suffix = f" | {' | '.join(meta_parts)}" if meta_parts else ""
        lines.append(
            f"  {repo_label:<38} | {p.status:<8} | {count} entities{meta_suffix}"
        )

    return [TextContent(type="text", text="\n".join(lines))]


def _handle_get_token_savings(arguments, container):
    if arguments.get("reset", False):
        container.stats_tracker.reset()
        text = "Token savings statistics have been reset."
    else:
        text = container.stats_tracker.get_summary()
    return [TextContent(type="text", text=text)]


async def _handle_get_file_tree(
    arguments,
    metadata,
    repo_manager,
):

    repo = arguments.get("repo", "").strip()
    branch = arguments.get("branch", "").strip() or None

    # Auto-resolve repo if not provided and exactly one repo is indexed.
    org_id = None
    if not repo:
        resolved = _resolve_single_repo(metadata, org_id=org_id, branch=branch)
        if isinstance(resolved, str):
            return [TextContent(type="text", text=resolved)]
        repo, branch = resolved

    err = _validate_repo_format(repo)
    if err:
        return [TextContent(type="text", text=f"Error: {err}")]

    tree_depth = arguments.get("depth", 4)
    if not isinstance(tree_depth, int) or tree_depth < 1:
        tree_depth = 4
    tree_depth = min(tree_depth, 10)

    include_hidden = bool(arguments.get("include_hidden", False))

    project = metadata.resolve_project(repo, org_id=org_id, branch=branch)
    if not project:
        return [
            TextContent(
                type="text",
                text=f"Repository not indexed: {repo}. Run index_repo first.",
            )
        ]

    if project.status != "ready":
        return [
            TextContent(
                type="text",
                text=(
                    f"Repository not ready: {project.repo_full_name} "
                    f"(status: {project.status}). Wait for indexing to complete."
                ),
            )
        ]

    repo_dir = project.local_path
    loop = asyncio.get_running_loop()
    try:
        tree = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: build_file_tree(
                    repo_dir, depth=tree_depth, include_hidden=include_hidden
                ),
            ),
            timeout=30.0,
        )
    except asyncio.TimeoutError:
        return [
            TextContent(
                type="text", text="Error: get_file_tree timed out after 30 seconds"
            )
        ]
    return [TextContent(type="text", text=tree)]


async def _handle_get_source(
    arguments,
    metadata,
    repo_manager,
    container=None,
):
    repo = arguments.get("repo", "").strip()
    branch = arguments.get("branch", "").strip() or None

    # Validate branch name format when explicitly provided
    import re as _re

    _BRANCH_RE = _re.compile(r"^[a-zA-Z0-9._\-/]+$")
    if branch and not _BRANCH_RE.match(branch):
        return [
            TextContent(
                type="text",
                text=f"Error: Invalid branch name '{branch}'. Branch names may only contain alphanumeric characters, dots, dashes, underscores, and slashes.",
            )
        ]

    # Auto-resolve repo if not provided and exactly one repo is indexed.
    org_id = None
    if not repo:
        resolved = _resolve_single_repo(metadata, org_id=org_id, branch=branch)
        if isinstance(resolved, str):
            return [TextContent(type="text", text=resolved)]
        repo, branch = resolved

    err = _validate_repo_format(repo)
    if err:
        return [TextContent(type="text", text=f"Error: {err}")]

    file_path = arguments.get("file", "").strip()
    if not file_path:
        return [TextContent(type="text", text="Error: file is required")]

    start_line = arguments.get("start_line") or None
    end_line = arguments.get("end_line") or None
    max_lines = 500

    project = metadata.resolve_project(repo, org_id=org_id, branch=branch)
    if not project:
        return [
            TextContent(
                type="text",
                text=f"Repository not indexed: {repo}. Run index_repo first.",
            )
        ]

    if project.status != "ready":
        return [
            TextContent(
                type="text",
                text=(
                    f"Repository not ready: {project.repo_full_name} "
                    f"(status: {project.status}). Wait for indexing to complete."
                ),
            )
        ]

    function_name = arguments.get("function", "").strip()
    if function_name and container is not None:
        try:
            matches = container.store.get_by_name(
                project.project_id, function_name, file_path=file_path
            )
            if not matches:
                return [
                    TextContent(
                        type="text",
                        text=f"Entity '{function_name}' not found in {file_path}. Check the name and try again.",
                    )
                ]
            match = matches[0]
            start_line = match.line_start
            end_line = match.line_end
            max_lines = 2000
        except Exception as exc:
            return [TextContent(type="text", text=f"Error looking up function: {exc}")]

    repo_dir = project.local_path
    loop = asyncio.get_running_loop()
    try:
        content, meta = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: read_source(
                    repo_dir, file_path, start_line, end_line, max_lines=max_lines
                ),
            ),
            timeout=30.0,
        )
    except asyncio.TimeoutError:
        return [
            TextContent(
                type="text", text="Error: get_source timed out after 30 seconds"
            )
        ]
    except SourceReaderError as exc:
        return [TextContent(type="text", text=f"Error: {exc}")]

    header_parts = [
        f"File: {meta['file_path']}",
        f"Lines: {meta['start_line']}-{meta['end_line']} of {meta['total_lines']}",
    ]
    if meta["truncated"]:
        header_parts.append(
            f"(truncated — file has {meta['total_lines']} lines total; "
            "use start_line/end_line to read other sections)"
        )
    header = " | ".join(header_parts)
    text = f"{header}\n\n{content}"
    return [TextContent(type="text", text=text)]


async def _handle_expand_result(
    arguments: dict,
    search_cache: dict,
) -> list[TextContent]:
    if not search_cache or "results" not in search_cache:
        return [
            TextContent(
                type="text", text="No search results cached. Run search_code first."
            )
        ]

    rank = arguments.get("rank")
    if not isinstance(rank, int) or rank < 1:
        return [
            TextContent(
                type="text",
                text="Error: rank must be a positive integer (e.g. 1, 2, 3)",
            )
        ]

    results = search_cache["results"]
    if rank > len(results):
        return [
            TextContent(
                type="text",
                text=f"Error: only {len(results)} results cached. rank must be 1-{len(results)}",
            )
        ]

    result = results[rank - 1]
    entity = result.entity
    local_path = search_cache["local_path"]

    try:
        loop = asyncio.get_running_loop()
        content, meta = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: read_source(
                    local_path,
                    entity.file_path,
                    max(entity.line_start, 1),
                    entity.line_end,
                ),
            ),
            timeout=30.0,
        )
    except asyncio.TimeoutError:
        return [
            TextContent(
                type="text",
                text=f"Error: expand_result timed out reading {entity.file_path}",
            )
        ]
    except SourceReaderError as exc:
        return [TextContent(type="text", text=f"Error reading source: {exc}")]
    except Exception as exc:
        return [TextContent(type="text", text=f"Error: {exc}")]

    header = f"=== Result #{rank} - {entity.name} ===\n{entity.file_path}:{entity.line_start}-{entity.line_end}\n"

    truncation_note = ""
    if meta.get("truncated"):
        truncation_note = f"\n... (file continues - {meta['total_lines']} total lines)"

    # Sanitise content — strip surrogate characters that break JSON serialisation
    safe_content = content.encode("utf-8", errors="replace").decode("utf-8")

    return [
        TextContent(type="text", text=header + "\n" + safe_content + truncation_note)
    ]


async def _handle_delete_repo(
    arguments: dict,
    container,
    metadata,
    repo_manager,
) -> list[TextContent]:
    """Handle the ``delete_repo`` MCP tool.

    Removes the project from the metadata store, drops the LanceDB vector
    table, and optionally removes the cloned repository files from disk.
    """
    repo = arguments.get("repo", "").strip()
    if not repo:
        return [TextContent(type="text", text="Error: repo is required")]

    branch = arguments.get("branch", "").strip() or None
    remove_files = bool(arguments.get("remove_files", True))

    err = _validate_repo_format(repo)
    if err:
        return [TextContent(type="text", text=f"Error: {err}")]

    org_id = None
    project_id = _make_project_id(repo, branch)
    project = metadata.get_project(project_id)

    # If exact project_id doesn't match, try fuzzy resolve
    if project is None:
        project = metadata.resolve_project(repo, org_id=org_id, branch=branch)

    if project is None:
        return [
            TextContent(
                type="text",
                text=f"Repository not found: {repo}. Use list_repos to see indexed repos.",
            )
        ]

    steps: list[str] = []
    errors: list[str] = []

    # 1. Drop LanceDB vector table
    loop = asyncio.get_running_loop()
    try:
        await asyncio.wait_for(
            loop.run_in_executor(
                None, lambda: container.store.clear(project.project_id)
            ),
            timeout=30.0,
        )
        steps.append("Dropped vector index")
    except asyncio.TimeoutError:
        errors.append("Timed out dropping vector index (will be cleaned up later)")
    except Exception as exc:
        errors.append(f"Failed to drop vector index: {exc}")
        logger.warning(
            "Failed to drop LanceDB table for %s: %s", project.project_id, exc
        )

    # 2. Remove metadata record
    try:
        metadata.delete_project(project.project_id)
        steps.append("Removed metadata record")
    except Exception as exc:
        errors.append(f"Failed to remove metadata: {exc}")
        logger.warning("Failed to delete metadata for %s: %s", project.project_id, exc)

    # 3. Remove cloned files (optional)
    if remove_files:
        try:
            await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: repo_manager.delete(project.repo_full_name, project.branch),
                ),
                timeout=60.0,
            )
            steps.append("Removed cloned files")
        except asyncio.TimeoutError:
            errors.append("Timed out removing cloned files")
        except Exception as exc:
            errors.append(f"Failed to remove cloned files: {exc}")
            logger.warning(
                "Failed to delete cloned files for %s: %s", project.project_id, exc
            )

    repo_label = (
        f"{project.repo_full_name}@{project.branch}"
        if project.branch
        else project.repo_full_name
    )
    summary = f"Deleted {repo_label}: {', '.join(steps)}."
    if errors:
        summary += f" Warnings: {'; '.join(errors)}."

    logger.info("delete_repo repo=%s steps=%s", repo_label, steps)
    return [TextContent(type="text", text=summary)]


# --- Standalone CLI entry point ---


def _parse_args():
    """Parse CLI arguments for the MCP server."""
    import argparse

    parser = argparse.ArgumentParser(description="Clean MCP server")
    parser.add_argument(
        "--repo",
        help="Default repository in owner/repo format (e.g. 'lokus-ai/lokus'). "
        "When set, search_code uses this repo automatically.",
    )
    return parser.parse_args()


async def _async_main(default_repo: str | None = None):
    config_mod = __import__("clean.core.config", fromlist=["CleanConfig"])
    CleanConfig = config_mod.CleanConfig
    config = CleanConfig.from_env()

    repos_dir = config.api.repos_dir or str(Path.home() / ".clean" / "repos")
    db_path = config.api.db_path or str(Path.home() / ".clean" / "metadata.db")

    container = ServiceContainer(config)
    metadata = MetadataStore(db_path)
    repo_manager = RepoManager(repos_dir)

    server = create_server(
        container,
        metadata=metadata,
        repo_manager=repo_manager,
        repos_dir=repos_dir,
        default_repo=default_repo,
    )
    if default_repo:
        logger.info("Starting Clean MCP server (default repo: %s)", default_repo)
    else:
        logger.info("Starting Clean MCP server")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


def main():
    """Entry point for the clean CLI command."""
    if sys.version_info < (3, 10):
        sys.exit(
            f"Clean requires Python 3.10+. You have {sys.version_info.major}.{sys.version_info.minor}"
        )
    args = _parse_args()
    asyncio.run(_async_main(default_repo=args.repo))


if __name__ == "__main__":
    main()
