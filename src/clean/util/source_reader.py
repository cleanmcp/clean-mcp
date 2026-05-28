"""Source code reader utility for MCP get_source tool.

Reads files from cloned repository directories with line-number annotations,
bounded line ranges, and strict path-traversal prevention.
"""

from __future__ import annotations

import os

__all__ = ["read_source", "SourceReaderError"]


class SourceReaderError(Exception):
    """Raised when read_source cannot satisfy the request safely."""


def read_source(
    repo_dir: str,
    file_path: str,
    start_line: int | None = None,
    end_line: int | None = None,
    max_lines: int = 500,
) -> tuple[str, dict]:
    """Read source code from a file inside a cloned repository directory.

    Returns the file contents with line numbers formatted as ``  42 | code``,
    together with a metadata dict describing what was returned.

    Security:
        - Absolute ``file_path`` values are rejected outright.
        - Paths containing ``..`` components are rejected before any filesystem
          access (fast-fail, no reliance on OS normalisation alone).
        - Both ``repo_dir`` and the resolved target path are passed through
          ``os.path.realpath`` before the prefix check, defeating symlink-based
          traversal.

    Args:
        repo_dir: Absolute path to the cloned repository root on disk.
        file_path: Relative path to the target file within *repo_dir*.
        start_line: First line to return (1-indexed, inclusive). ``None``
            means start from line 1.
        end_line: Last line to return (1-indexed, inclusive). ``None`` means
            extend to ``start_line + max_lines - 1`` (or EOF for short files).
        max_lines: Hard ceiling on the number of lines returned per call.
            Defaults to 500.

    Returns:
        A two-tuple ``(content, metadata)`` where:

        - *content* is the annotated source text (``  N | line``)
        - *metadata* is a dict with keys:
            - ``file_path`` — the relative path as supplied
            - ``total_lines`` — total lines in the file
            - ``returned_lines`` — number of lines in *content*
            - ``start_line`` — actual first line returned (1-indexed)
            - ``end_line`` — actual last line returned (1-indexed)
            - ``truncated`` — ``True`` when not all available lines were returned

    Raises:
        SourceReaderError: For security violations, missing files, or
            non-file targets (directories, devices, etc.).
    """
    # ------------------------------------------------------------------
    # Input validation — security-critical, reject early
    # ------------------------------------------------------------------

    if not file_path or not isinstance(file_path, str):
        raise SourceReaderError("file_path must be a non-empty string")

    if os.path.isabs(file_path):
        raise SourceReaderError("file_path must be a relative path, not absolute")

    # Reject any path component that is exactly ".." regardless of OS
    # separator to provide defence-in-depth before realpath resolution.
    parts = file_path.replace("\\", "/").split("/")
    if ".." in parts:
        raise SourceReaderError("file_path must not contain '..' components")

    if not repo_dir or not isinstance(repo_dir, str):
        raise SourceReaderError("repo_dir must be a non-empty string")

    # ------------------------------------------------------------------
    # Resolve both sides with realpath (follows symlinks)
    # ------------------------------------------------------------------

    real_base = os.path.realpath(repo_dir)
    candidate = os.path.join(real_base, file_path)
    real_target = os.path.realpath(candidate)

    # The target must be strictly inside the repo root (or equal to it,
    # though reading a directory is caught below).
    prefix = real_base.rstrip(os.sep) + os.sep
    if real_target != real_base and not real_target.startswith(prefix):
        raise SourceReaderError(
            f"file_path '{file_path}' resolves outside the repository root"
        )

    # ------------------------------------------------------------------
    # File existence and type checks
    # ------------------------------------------------------------------

    if not os.path.exists(real_target):
        raise SourceReaderError(f"File not found: {file_path}")

    if not os.path.isfile(real_target):
        raise SourceReaderError(f"Path is not a regular file: {file_path}")

    # ------------------------------------------------------------------
    # Read file
    # ------------------------------------------------------------------

    with open(real_target, encoding="utf-8", errors="replace") as fh:
        all_lines = fh.readlines()

    total_lines = len(all_lines)

    # ------------------------------------------------------------------
    # Resolve line range (convert to 0-based indices)
    # ------------------------------------------------------------------

    # Validate start_line (1-indexed).
    if start_line is not None:
        if int(start_line) < 1:
            raise SourceReaderError(
                f"start_line must be >= 1 (lines are 1-indexed); got {start_line}"
            )
        actual_start = int(start_line)
    else:
        actual_start = 1

    if end_line is not None:
        actual_end = max(actual_start, int(end_line))
    else:
        actual_end = actual_start + max_lines - 1

    # Never exceed the file length.
    actual_end = min(actual_end, total_lines)
    # Also enforce max_lines ceiling from the resolved start.
    capped_end = min(actual_end, actual_start + max_lines - 1)

    truncated = capped_end < actual_end or (
        end_line is None and actual_end < total_lines
    )
    actual_end = capped_end

    # Convert to 0-based slice indices.
    slice_start = actual_start - 1
    slice_end = actual_end  # exclusive

    selected_lines = all_lines[slice_start:slice_end]

    # ------------------------------------------------------------------
    # Format with line numbers: "  42 | <code>"
    # ------------------------------------------------------------------

    width = len(str(total_lines))
    annotated_lines = [
        f"{slice_start + i + 1:{width}} | {line.rstrip()}"
        for i, line in enumerate(selected_lines)
    ]
    content = "\n".join(annotated_lines)

    metadata: dict = {
        "file_path": file_path,
        "total_lines": total_lines,
        "returned_lines": len(selected_lines),
        "start_line": actual_start,
        "end_line": actual_end,
        "truncated": truncated,
    }

    return content, metadata
