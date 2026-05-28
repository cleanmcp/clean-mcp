"""RepoManager — git clone, pull, delete for GitHub repos using git worktrees."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

from ..core.errors import RepoError
from ..util.logging import get_logger
from ..util.security import validate_path

logger = get_logger(__name__)

GIT_TIMEOUT = int(os.environ.get("CLEAN_GIT_TIMEOUT", "600"))  # 10 min default

_REPO_NAME_RE = re.compile(r"^[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+$")
_BRANCH_RE = re.compile(r"^[a-zA-Z0-9._\-/]+$")
_TOKEN_RE = re.compile(r"(ghs_[A-Za-z0-9_]+|ghp_[A-Za-z0-9_]+|x-access-token:[^\s@]+)")
_URL_TOKEN_RE = re.compile(r"://[^@]+@")

# Patterns used to block SSRF via repo owner/name fields
_IPV4_RE = re.compile(r"^[0-9]+(\.[0-9]+)+$")  # e.g. 169.254.169.254
_NUMERIC_RE = re.compile(r"^[0-9]+$")  # purely numeric names
_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "metadata",
        "metadata.google.internal",
        "instance-data",
        "169.254.169.254",
    }
)

_META_FILE = ".clean-meta.json"


def _scrub_tokens(text: str) -> str:
    """Remove GitHub tokens from error messages."""
    scrubbed = _TOKEN_RE.sub("***", text)
    scrubbed = _URL_TOKEN_RE.sub("://***@", scrubbed)
    return scrubbed


def _inject_token(clone_url: str, token: str) -> str:
    """Embed an access token into a clone URL.

    Transforms ``https://github.com/o/r.git`` into
    ``https://x-access-token:<token>@github.com/o/r.git``.
    The token only lives in process memory — never on disk.
    """
    return clone_url.replace("https://", f"https://x-access-token:{token}@", 1)


def _is_ssrf_name(name: str) -> bool:
    """Return True if *name* looks like an IP address or internal hostname.

    Blocks dotted-quad IPs (``169.254.169.254``), purely numeric names,
    and well-known internal hostnames used for cloud metadata endpoints.
    """
    lower = name.lower()
    if lower in _BLOCKED_HOSTNAMES:
        return True
    if _IPV4_RE.match(lower):
        return True
    if _NUMERIC_RE.match(lower):
        return True
    return False


def validate_repo_name(full_name: str) -> str:
    """Validate that a repo name matches 'owner/repo' with safe characters only.

    Also rejects IP addresses and internal hostnames to prevent SSRF.
    Raises RepoError if the name is invalid.
    """
    if not full_name or not _REPO_NAME_RE.match(full_name):
        raise RepoError(
            f"Invalid repository name: '{full_name}'. "
            "Expected 'owner/repo' with alphanumeric, dot, dash, or underscore characters only."
        )
    owner, repo = full_name.split("/", 1)
    if ".." in owner or ".." in repo:
        raise RepoError(
            f"Invalid repository name: '{full_name}'. "
            "Owner and repo names must not contain '..' components."
        )
    if _is_ssrf_name(owner) or _is_ssrf_name(repo):
        raise RepoError(
            f"Invalid repository name: '{full_name}'. "
            "Owner and repo names must not be IP addresses or internal hostnames."
        )
    return full_name


def validate_branch_name(branch: str) -> str:
    """Validate a git branch name. Raises RepoError if invalid."""
    if not branch or not _BRANCH_RE.match(branch):
        raise RepoError(
            f"Invalid branch name: '{branch}'. "
            "Use alphanumeric characters, dots, dashes, underscores, or slashes."
        )
    return branch


class RepoManager:
    """Manages local clones of GitHub repositories using git worktrees.

    Each repository is cloned once (the main/default branch). Additional
    branches are handled via ``git worktree add`` inside the base clone
    directory at ``.worktrees/{safe_branch}``, avoiding redundant full
    clones and saving disk space.
    """

    def __init__(self, repos_dir: str) -> None:
        self._repos_dir = repos_dir
        Path(repos_dir).mkdir(parents=True, exist_ok=True)
        self._repo_locks: dict[str, threading.Lock] = {}
        self._locks_mutex = threading.Lock()

    # ------------------------------------------------------------------
    # Lock helpers
    # ------------------------------------------------------------------

    def _repo_lock(self, full_name: str) -> threading.Lock:
        """Return a per-repo threading.Lock, creating it on first access."""
        with self._locks_mutex:
            if full_name not in self._repo_locks:
                self._repo_locks[full_name] = threading.Lock()
            return self._repo_locks[full_name]

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _base_path(self, full_name: str) -> str:
        """Return the base clone path for a repo (always owner/repo, no branch suffix).

        This is always the primary worktree root regardless of which branch
        is being operated on.
        """
        validate_repo_name(full_name)
        path = os.path.join(self._repos_dir, full_name)
        validate_path(path, self._repos_dir)
        return path

    def _get_default_branch(self, full_name: str) -> str:
        """Read the default branch from .clean-meta.json in the base clone.

        Falls back to ``"main"`` if the file is absent or unreadable.
        """
        meta_path = os.path.join(self._base_path(full_name), _META_FILE)
        try:
            with open(meta_path) as fh:
                data = json.load(fh)
            return str(data.get("default_branch", "main"))
        except (OSError, json.JSONDecodeError, KeyError):
            # File absent (e.g. _write_meta failed after a partial clone) or
            # unreadable — fall back to "main" so repo_path() stays functional.
            return "main"

    def _write_meta(self, full_name: str, default_branch: str) -> None:
        """Write .clean-meta.json into the base clone root."""
        meta_path = os.path.join(self._base_path(full_name), _META_FILE)
        try:
            with open(meta_path, "w") as fh:
                json.dump({"default_branch": default_branch}, fh)
        except OSError as exc:
            logger.warning("Could not write %s for %s: %s", _META_FILE, full_name, exc)

    def repo_path(self, full_name: str, branch: str | None = None) -> str:
        """Return the local filesystem path for a repo / branch combination.

        - ``branch`` is ``None`` or matches the default branch →
          ``repos/{owner}/{repo}``  (base clone, primary worktree)
        - ``branch`` is a non-default branch →
          ``repos/{owner}/{repo}/.worktrees/{safe_branch}``

        All paths are validated against traversal attacks.
        """
        validate_repo_name(full_name)
        base = self._base_path(full_name)

        if branch:
            validate_branch_name(branch)
            default_branch = self._get_default_branch(full_name)
            if branch != default_branch:
                safe_branch = branch.replace("/", "_")
                wt_path = os.path.join(base, ".worktrees", safe_branch)
                validate_path(wt_path, self._repos_dir)
                return wt_path

        return base

    # ------------------------------------------------------------------
    # Existence check
    # ------------------------------------------------------------------

    def exists(self, full_name: str, branch: str | None = None) -> bool:
        """Check whether a repo/worktree exists on disk.

        Accepts both a ``.git`` directory (normal clone / primary worktree)
        and a ``.git`` file (git worktree checkout).
        """
        dest = self.repo_path(full_name, branch)
        git_path = os.path.join(dest, ".git")
        return os.path.isdir(git_path) or os.path.isfile(git_path)

    # ------------------------------------------------------------------
    # Clone
    # ------------------------------------------------------------------

    def clone(
        self,
        clone_url: str,
        full_name: str,
        token: str | None = None,
        branch: str | None = None,
    ) -> str:
        """Clone a repository (blobless). If already exists, pull instead.

        Uses ``--filter=blob:none --single-branch`` so that the clone is
        history-aware (required for worktrees) while still keeping the
        initial download small.

        When *token* is provided, it is embedded in the clone URL in memory
        only — it is never written to disk.  Subprocess stderr is captured
        and scrubbed before logging to prevent token leaks.

        When *branch* is provided and the base clone already exists, the
        method delegates to ``create_worktree()`` rather than attempting a
        second full clone.
        """
        validate_repo_name(full_name)
        base = self._base_path(full_name)
        effective_branch = branch or "main"

        with self._repo_lock(full_name):
            # Determine whether this is a worktree request.
            base_exists = os.path.isdir(os.path.join(base, ".git"))

            if branch:
                validate_branch_name(branch)
                default_branch = (
                    self._get_default_branch(full_name) if base_exists else branch
                )
                is_non_default = branch != default_branch
            else:
                is_non_default = False

            # --- Case 1: full clone already present for the default branch ---
            if base_exists and not is_non_default:
                logger.info("Repo already cloned, pulling: %s", full_name)
                self._pull_locked(full_name, token=token, branch=branch)
                return base

            # --- Case 2: base clone exists, caller wants a non-default branch ---
            if base_exists and is_non_default:
                logger.info(
                    "Base clone exists; creating worktree for %s@%s", full_name, branch
                )
                return self._create_worktree_locked(full_name, branch, token=token)  # type: ignore[arg-type]

            # --- Case 3: nothing on disk — do a fresh blobless clone ---
            # Clean up any partial clone directory left by a previous failed attempt.
            if os.path.isdir(base) and not base_exists:
                logger.warning("Removing partial clone dir: %s", base)
                shutil.rmtree(base)

            Path(base).parent.mkdir(parents=True, exist_ok=True)

            env = os.environ.copy()
            env["GIT_TERMINAL_PROMPT"] = "0"

            effective_url = _inject_token(clone_url, token) if token else clone_url

            cmd = [
                "git",
                "clone",
                "--filter=blob:none",
                "--single-branch",
            ]
            if branch:
                cmd.extend(["--branch", branch])
            cmd.extend([effective_url, base])

            try:
                subprocess.run(
                    cmd,
                    check=True,
                    capture_output=True,
                    timeout=GIT_TIMEOUT,
                    env=env,
                )
            except subprocess.CalledProcessError as exc:
                raise RepoError(
                    f"Clone failed for {full_name}: {_scrub_tokens(exc.stderr.decode())}"
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise RepoError(f"Clone timed out for {full_name}") from exc

            # After a successful authenticated clone the remote URL contains
            # the token.  Reset it to the plain URL so the token is not
            # persisted in .git/config.
            if token:
                self._update_remote_url_locked(full_name, clone_url)

            # Record the default branch so future calls to repo_path() know
            # which branch is "main" without querying git.
            self._write_meta(full_name, effective_branch)

            logger.info(
                "Cloned %s to %s (branch: %s)", full_name, base, effective_branch
            )
            return base

    # ------------------------------------------------------------------
    # Pull
    # ------------------------------------------------------------------

    def pull(
        self, full_name: str, token: str | None = None, branch: str | None = None
    ) -> None:
        """Pull latest changes (fast-forward only).

        For worktrees (non-default branch):
        - ``git fetch origin {branch}`` runs against the **base** clone so
          that the remote-tracking ref is updated.
        - ``git merge --ff-only origin/{branch}`` runs inside the worktree
          to advance the working copy.

        When *token* is provided the remote URL is temporarily set to an
        authenticated URL, then reset after the operation completes (or
        fails) so the token is never persisted on disk.
        """
        if not self.exists(full_name, branch):
            raise RepoError(f"Repo not cloned: {full_name}")

        with self._repo_lock(full_name):
            self._pull_locked(full_name, token=token, branch=branch)

    def _pull_locked(
        self, full_name: str, token: str | None = None, branch: str | None = None
    ) -> None:
        """Pull implementation — caller must hold the repo lock."""
        base = self._base_path(full_name)
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"

        default_branch = self._get_default_branch(full_name)
        is_worktree = bool(branch and branch != default_branch)
        wt_path = self.repo_path(full_name, branch) if is_worktree else base

        plain_url: str | None = None
        if token:
            # Read the current remote URL from the base clone .git/config.
            try:
                result = subprocess.run(
                    ["git", "remote", "get-url", "origin"],
                    cwd=base,
                    check=True,
                    capture_output=True,
                    timeout=30,
                )
                plain_url = result.stdout.decode().strip()
            except subprocess.CalledProcessError:
                plain_url = None

            if plain_url:
                authed_url = _inject_token(plain_url, token)
                self._update_remote_url_locked(full_name, authed_url)

        try:
            if is_worktree:
                # Step 1: fetch into base clone so origin/{branch} advances.
                subprocess.run(
                    ["git", "fetch", "origin", branch],
                    cwd=base,
                    check=True,
                    capture_output=True,
                    timeout=GIT_TIMEOUT,
                    env=env,
                )
                # Step 2: fast-forward the worktree checkout.
                subprocess.run(
                    ["git", "merge", "--ff-only", f"origin/{branch}"],
                    cwd=wt_path,
                    check=True,
                    capture_output=True,
                    timeout=GIT_TIMEOUT,
                    env=env,
                )
            else:
                subprocess.run(
                    ["git", "pull", "--ff-only"],
                    cwd=base,
                    check=True,
                    capture_output=True,
                    timeout=GIT_TIMEOUT,
                    env=env,
                )
        except subprocess.CalledProcessError as exc:
            raise RepoError(
                f"Pull failed for {full_name}: {_scrub_tokens(exc.stderr.decode())}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RepoError(f"Pull timed out for {full_name}") from exc
        finally:
            # Always restore the plain (non-authenticated) remote URL.
            if token and plain_url:
                try:
                    self._update_remote_url_locked(full_name, plain_url)
                except RepoError:
                    pass

        logger.info("Pulled %s (branch: %s)", full_name, branch or default_branch)

    # ------------------------------------------------------------------
    # Worktree management
    # ------------------------------------------------------------------

    def create_worktree(
        self, full_name: str, branch: str, token: str | None = None
    ) -> str:
        """Fetch *branch* from origin and create a git worktree for it.

        Args:
            full_name: Repository in ``owner/repo`` format.
            branch: The branch to check out in the new worktree.
            token: Optional GitHub access token for authentication.

        Returns:
            Absolute path to the newly created (or already-existing) worktree.

        Raises:
            RepoError: If the base clone does not exist, inputs are invalid,
                or the underlying git commands fail.
        """
        validate_repo_name(full_name)
        validate_branch_name(branch)

        with self._repo_lock(full_name):
            return self._create_worktree_locked(full_name, branch, token=token)

    def _create_worktree_locked(
        self, full_name: str, branch: str, token: str | None = None
    ) -> str:
        """Worktree creation — caller must hold the repo lock."""
        base = self._base_path(full_name)

        if not os.path.isdir(os.path.join(base, ".git")):
            raise RepoError(
                f"Base clone for {full_name} does not exist. Clone the repo first."
            )

        safe_branch = branch.replace("/", "_")
        wt_path = os.path.join(base, ".worktrees", safe_branch)
        validate_path(wt_path, self._repos_dir)

        # Already a valid worktree — nothing to do.
        if os.path.isfile(os.path.join(wt_path, ".git")):
            logger.info("Worktree already exists for %s@%s", full_name, branch)
            return wt_path

        # Directory exists but is not a proper worktree — clean up.
        if os.path.isdir(wt_path):
            logger.warning("Removing incomplete worktree dir: %s", wt_path)
            shutil.rmtree(wt_path)

        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"

        plain_url: str | None = None
        if token:
            try:
                result = subprocess.run(
                    ["git", "remote", "get-url", "origin"],
                    cwd=base,
                    check=True,
                    capture_output=True,
                    timeout=30,
                )
                plain_url = result.stdout.decode().strip()
            except subprocess.CalledProcessError:
                plain_url = None

            if plain_url:
                self._update_remote_url_locked(
                    full_name, _inject_token(plain_url, token)
                )

        try:
            # Fetch the branch so origin/{branch} exists as a tracking ref.
            subprocess.run(
                ["git", "fetch", "origin", branch],
                cwd=base,
                check=True,
                capture_output=True,
                timeout=GIT_TIMEOUT,
                env=env,
            )
            # Create the worktree under .worktrees/{safe_branch}.
            subprocess.run(
                [
                    "git",
                    "worktree",
                    "add",
                    os.path.join(".worktrees", safe_branch),
                    f"origin/{branch}",
                ],
                cwd=base,
                check=True,
                capture_output=True,
                timeout=GIT_TIMEOUT,
                env=env,
            )
        except subprocess.CalledProcessError as exc:
            raise RepoError(
                f"create_worktree failed for {full_name}@{branch}: "
                f"{_scrub_tokens(exc.stderr.decode())}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RepoError(
                f"create_worktree timed out for {full_name}@{branch}"
            ) from exc
        finally:
            if token and plain_url:
                try:
                    self._update_remote_url_locked(full_name, plain_url)
                except RepoError:
                    pass

        logger.info("Created worktree for %s@%s at %s", full_name, branch, wt_path)
        return wt_path

    def remove_worktree(self, full_name: str, branch: str) -> None:
        """Remove a git worktree. No-op if the worktree does not exist.

        Args:
            full_name: Repository in ``owner/repo`` format.
            branch: The branch whose worktree should be removed.
        """
        validate_repo_name(full_name)
        validate_branch_name(branch)

        base = self._base_path(full_name)
        safe_branch = branch.replace("/", "_")
        wt_path = os.path.join(base, ".worktrees", safe_branch)

        if not os.path.exists(wt_path):
            return

        with self._repo_lock(full_name):
            try:
                subprocess.run(
                    [
                        "git",
                        "worktree",
                        "remove",
                        os.path.join(".worktrees", safe_branch),
                        "--force",
                    ],
                    cwd=base,
                    check=True,
                    capture_output=True,
                    timeout=GIT_TIMEOUT,
                )
                logger.info("Removed worktree for %s@%s", full_name, branch)
            except subprocess.CalledProcessError:
                logger.warning(
                    "git worktree remove failed for %s@%s; falling back to prune + rmtree",
                    full_name,
                    branch,
                )
                try:
                    subprocess.run(
                        ["git", "worktree", "prune"],
                        cwd=base,
                        check=False,
                        capture_output=True,
                        timeout=30,
                    )
                except Exception:
                    pass
                if os.path.isdir(wt_path):
                    shutil.rmtree(wt_path)
                logger.info(
                    "Cleaned up worktree dir for %s@%s via rmtree", full_name, branch
                )

    def list_worktrees(self, full_name: str) -> list[str]:
        """Return branch names of all active worktrees (excludes the main worktree).

        Args:
            full_name: Repository in ``owner/repo`` format.

        Returns:
            List of branch name strings, e.g. ``["feat/foo", "fix/bar"]``.

        Raises:
            RepoError: If the base clone does not exist or git fails.
        """
        validate_repo_name(full_name)
        base = self._base_path(full_name)

        if not os.path.isdir(os.path.join(base, ".git")):
            raise RepoError(f"Base clone for {full_name} does not exist.")

        try:
            result = subprocess.run(
                ["git", "worktree", "list", "--porcelain"],
                cwd=base,
                check=True,
                capture_output=True,
                timeout=30,
            )
        except subprocess.CalledProcessError as exc:
            raise RepoError(
                f"list_worktrees failed for {full_name}: {exc.stderr.decode().strip()}"
            ) from exc

        output = result.stdout.decode()
        branches: list[str] = []
        current_worktree_path: str | None = None

        for raw_line in output.splitlines():
            line = raw_line.strip()
            if line.startswith("worktree "):
                current_worktree_path = line[len("worktree ") :].strip()
            elif line.startswith("branch refs/heads/"):
                branch_name = line[len("branch refs/heads/") :].strip()
                # Skip the main worktree (base clone path).
                if current_worktree_path and os.path.realpath(
                    current_worktree_path
                ) != os.path.realpath(base):
                    branches.append(branch_name)

        return branches

    # ------------------------------------------------------------------
    # Remote URL management
    # ------------------------------------------------------------------

    def update_remote_url(
        self, full_name: str, new_url: str, branch: str | None = None
    ) -> None:
        """Update the remote origin URL (e.g. to refresh auth token).

        The remote URL always lives in the **base** clone's ``.git/config``,
        regardless of which branch/worktree is being updated.  The *branch*
        parameter is accepted for API compatibility but is intentionally
        unused for path resolution.
        """
        if not self.exists(full_name):
            raise RepoError(f"Repo not cloned: {full_name}")

        with self._repo_lock(full_name):
            self._update_remote_url_locked(full_name, new_url)

    def _update_remote_url_locked(self, full_name: str, new_url: str) -> None:
        """Update remote URL — caller must hold the repo lock."""
        base = self._base_path(full_name)
        try:
            subprocess.run(
                ["git", "remote", "set-url", "origin", new_url],
                cwd=base,
                check=True,
                capture_output=True,
                timeout=30,
            )
        except subprocess.CalledProcessError as exc:
            raise RepoError(
                f"Failed to update remote for {full_name}: "
                f"{_scrub_tokens(exc.stderr.decode())}"
            ) from exc

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def delete(self, full_name: str, branch: str | None = None) -> None:
        """Remove a cloned repo or a specific branch worktree.

        Behaviour:
        - ``branch`` is ``None``: remove the entire base clone (and all worktrees).
        - ``branch`` matches the default branch: same as above — remove everything.
        - ``branch`` is a non-default branch: remove only that worktree via
          ``remove_worktree()``.
        """
        validate_repo_name(full_name)

        if branch:
            validate_branch_name(branch)
            default_branch = self._get_default_branch(full_name)
            if branch != default_branch:
                # Remove just the worktree, keep the base clone.
                self.remove_worktree(full_name, branch)
                return

        # Remove the entire base clone directory (includes all worktrees).
        base = self._base_path(full_name)
        if os.path.isdir(base):
            with self._repo_lock(full_name):
                if os.path.isdir(base):
                    shutil.rmtree(base)
                    logger.info("Deleted %s", base)
