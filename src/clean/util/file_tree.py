"""File tree builder for repository directory structures."""

from __future__ import annotations

import os

# Directories that are never useful for code navigation.
_SKIP_DIRS: frozenset[str] = frozenset(
    {
        "node_modules",
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        "env",
        ".env",
        "dist",
        "build",
        ".next",
        ".nuxt",
        "coverage",
        ".pytest_cache",
        ".mypy_cache",
        ".tox",
        ".eggs",
        ".cache",
        ".turbo",
        ".vercel",
        ".output",
    }
)


def _should_skip(name: str, include_hidden: bool) -> bool:
    """Return True when *name* is a directory that should be skipped.

    Args:
        name: Directory basename to test.
        include_hidden: When False, directories that start with '.' are
            skipped unless they already appear in the always-skip set.

    Returns:
        True when the directory should be excluded from the tree.
    """
    if name in _SKIP_DIRS:
        return True
    if name.endswith(".egg-info"):
        return True
    if not include_hidden and name.startswith("."):
        return True
    return False


def build_file_tree(
    repo_dir: str,
    depth: int = 4,
    include_hidden: bool = False,
) -> str:
    """Return an indented tree view of *repo_dir* up to *depth* levels.

    Directories appear before files at every level; both groups are sorted
    alphabetically.  The tree uses the familiar ``├──`` / ``└──`` / ``│``
    box-drawing characters.

    Args:
        repo_dir: Absolute path to the repository root on disk.
        depth: Maximum number of directory levels to descend.  The root
            itself is level 0; its immediate children are at depth 1.
        include_hidden: When True, hidden directories (those whose names
            start with ``.``) that are not in the always-skip list are
            included.  Defaults to False.

    Returns:
        A multi-line string representing the directory tree, or an error
        message when *repo_dir* does not exist or is not a directory.
    """
    if not os.path.isdir(repo_dir):
        return f"Error: directory not found: {repo_dir}"

    lines: list[str] = [os.path.basename(repo_dir) + "/"]
    _seen: set[str] = {os.path.realpath(repo_dir)}
    _MAX_DEPTH = 20

    def _walk(current_dir: str, prefix: str, current_depth: int) -> None:
        if current_depth > _MAX_DEPTH:
            return
        try:
            raw_entries = os.listdir(current_dir)
        except PermissionError:
            return

        dirs = sorted(
            e
            for e in raw_entries
            if os.path.isdir(os.path.join(current_dir, e))
            and not _should_skip(e, include_hidden)
        )
        files = sorted(
            e for e in raw_entries if os.path.isfile(os.path.join(current_dir, e))
        )

        # Directories first, then files — both alphabetical.
        all_entries: list[tuple[str, bool]] = [(d, True) for d in dirs] + [
            (f, False) for f in files
        ]

        for idx, (name, is_dir) in enumerate(all_entries):
            is_last = idx == len(all_entries) - 1
            connector = "└──" if is_last else "├──"
            label = name + "/" if is_dir else name
            lines.append(f"{prefix}{connector} {label}")

            if is_dir:
                child_extension = "    " if is_last else "│   "
                if current_depth < depth:
                    child_path = os.path.join(current_dir, name)
                    real_child = os.path.realpath(child_path)
                    if real_child in _seen:
                        continue  # symlink loop — skip
                    _seen.add(real_child)
                    _walk(
                        child_path,
                        prefix + child_extension,
                        current_depth + 1,
                    )
                else:
                    # At the depth limit — peek to see if there is content below.
                    try:
                        child_entries = os.listdir(os.path.join(current_dir, name))
                    except PermissionError:
                        child_entries = []
                    has_children = any(
                        not _should_skip(e, include_hidden) for e in child_entries
                    )
                    if has_children:
                        lines.append(f"{prefix}{child_extension}└── ...")

    _walk(repo_dir, "", 1)
    return "\n".join(lines)
