# Clean

> Local semantic code search for AI coding agents — runs on your laptop, indexes stay on disk.

Clean is an [MCP](https://modelcontextprotocol.io) server that gives Claude Code, Cursor, and other AI tools **meaning-aware** code search. It parses your repositories with tree-sitter, builds a call graph, embeds every function with a local sentence-transformer model, and stores everything in [LanceDB](https://lancedb.com) — no cloud, no API keys, no telemetry.

```text
"find the function that validates email on signup"
   ↓
search_code(query="email validation on signup")
   ↓
returns the right function with full source + callers/callees
```

## Features

- **Semantic search** — describes *behaviour*, not keywords; finds code by what it does.
- **Local-only** — embeddings, metadata, and source files live in `~/.clean/`. Nothing leaves your machine.
- **MCP-native** — drops into Claude Code / Cursor / any MCP client over stdio.
- **Index anything** — point it at a local folder *or* a public GitHub repo.
- **Tree-sitter parsing** — Python, JavaScript, TypeScript.
- **Call graph aware** — search results include direct callers and callees.

## Installation

Requires Python 3.10–3.13.

```bash
git clone https://github.com/CodeWithInferno/Clean.git
cd Clean
pip install -e .
```

First run downloads `all-MiniLM-L6-v2` (~90 MB) into `~/.cache/huggingface/`. After that, startup is fast.

## Wire it into your MCP client

### Claude Code / Cursor — `.mcp.json` (project-scoped)

```json
{
  "mcpServers": {
    "clean": {
      "command": "python",
      "args": ["-m", "clean.local.mcp_server"]
    }
  }
}
```

Or globally via the CLI: `claude mcp add clean -- python -m clean.local.mcp_server`

## Tools

| Tool | What it does |
|------|--------------|
| `index_repo` | Index a folder (`path`) or clone+index a GitHub repo (`repo`) |
| `search_code` | Semantic search across indexed code |
| `list_repos` | Show every indexed repository |
| `get_file_tree` | Print the directory tree of an indexed repo |
| `get_source` | Read a file (or named function) from an indexed repo |
| `expand_result` | Get full source for a truncated search result |
| `delete_repo` | Remove an index + optionally its source files |
| `get_token_savings` | Show TOON-format token savings |

## Example usage in Claude Code

> "Index this directory" → calls `index_repo` with the current path
>
> "Find the function that handles login redirects" → `search_code`
>
> "Show me how the indexer entry point works" → `search_code` then `get_source`

## Where your data lives

```
~/.clean/
├── index/         LanceDB vector store
├── metadata.db    SQLite — which repos are indexed, status
└── repos/         git clones (only for GitHub-mode indexing)
```

Back up that folder to keep your indexes. Delete it to start fresh.

Override the location with env vars:

| Variable | Default |
|----------|---------|
| `CLEAN_REPOS_DIR` | `~/.clean/repos` |
| `CLEAN_DB_PATH` | `~/.clean/metadata.db` |
| `CLEAN_PERSIST_PATH` | `~/.clean/index` |

## Development

```bash
make install   # creates .venv and installs deps
make test      # runs the test suite
make lint      # ruff check + format check
make format    # apply ruff fixes
```

## Contributing

PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).
