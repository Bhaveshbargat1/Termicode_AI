"""Version control, automated Git checkpoints, and rollback system for TermiCode AI."""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field


class GitError(Exception):
    """Base exception for Git and version control failures."""


class GitRepositoryError(GitError):
    """Raised when an operation requires a Git repository but none exists."""


class CheckpointNotFoundError(GitError):
    """Raised when a specified checkpoint ID cannot be found."""


class GitCheckpoint(BaseModel):
    """Represents a saved Git checkpoint."""

    id: str = Field(description="Unique checkpoint identifier (e.g. cp_a1b2c3d4).")
    timestamp: str = Field(description="ISO 8601 UTC timestamp of creation.")
    message: str = Field(description="Human-readable description of the checkpoint.")
    commit_hash: str = Field(description="Git HEAD commit hash at checkpoint creation.")
    has_stash: bool = Field(default=False, description="Whether uncommitted changes were stashed.")
    stash_oid: str | None = Field(
        default=None, description="Stash commit OID if uncommitted changes exist."
    )
    type: str = Field(default="manual", description="Checkpoint type: 'manual' or 'auto'.")
    files: list[str] = Field(
        default_factory=list, description="List of uncommitted files at checkpoint time."
    )


def is_git_installed() -> bool:
    """Return True if git executable is available in system PATH."""
    return shutil.which("git") is not None


def run_git(args: list[str], root: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Execute a git command in root directory, capturing output safely."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            check=check,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.CalledProcessError as err:
        stderr_msg = err.stderr.strip() if err.stderr else str(err)
        raise GitError(f"Git command 'git {' '.join(args)}' failed: {stderr_msg}") from err
    except Exception as err:
        raise GitError(f"Failed to execute git command: {err}") from err


def is_git_repo(root: Path) -> bool:
    """Check if root workspace is inside a valid Git repository."""
    if not (root / ".git").exists():
        try:
            res = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=root,
                capture_output=True,
                text=True,
            )
            return res.returncode == 0 and res.stdout.strip() == "true"
        except Exception:
            return False
    return True


def get_head_commit(root: Path) -> str | None:
    """Return the current HEAD commit hash, or None if repo has no commits."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
        return None
    except Exception:
        return None


def get_uncommitted_changes(root: Path) -> list[str]:
    """Return list of modified, added, deleted, or untracked file paths."""
    try:
        res = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception:
        return []

    changed: list[str] = []
    for line in res.stdout.splitlines():
        trimmed = line.strip()
        if not trimmed:
            continue
        parts = line[2:].strip()
        if not parts.startswith(".termicode"):
            changed.append(parts)
    return changed


def has_uncommitted_changes(root: Path) -> bool:
    """Return True if working tree has any uncommitted changes."""
    return len(get_uncommitted_changes(root)) > 0


def init_git_repo(root: Path) -> tuple[bool, str]:
    """Initialize a Git repository in root workspace with initial commit and .gitignore."""
    if is_git_repo(root):
        return False, "Workspace is already a Git repository."

    run_git(["init"], root)

    # Ensure local git config has minimal identity so commits don't fail in blank environments
    try:
        run_git(["config", "user.name"], root)
    except GitError:
        run_git(["config", "user.name", "TermiCode User"], root)

    try:
        run_git(["config", "user.email"], root)
    except GitError:
        run_git(["config", "user.email", "termicode@local.dev"], root)

    # Ensure .gitignore exists and ignores .termicode/
    gitignore_file = root / ".gitignore"
    if not gitignore_file.exists():
        gitignore_file.write_text(".termicode/\n__pycache__/\n*.pyc\n.venv/\n", encoding="utf-8")
    else:
        existing = gitignore_file.read_text(encoding="utf-8")
        if ".termicode" not in existing:
            gitignore_file.write_text(existing.rstrip() + "\n.termicode/\n", encoding="utf-8")

    # If repository has no commits yet, create initial empty commit so HEAD exists
    if get_head_commit(root) is None:
        try:
            run_git(["add", ".gitignore"], root)
            run_git(["commit", "-m", "Initial commit by TermiCode"], root)
        except Exception:
            run_git(["commit", "--allow-empty", "-m", "Initial commit by TermiCode"], root)

    return True, "Initialized Git repository with initial commit."


class CheckpointManager:
    """Manages Git-based state snapshots, stashes, and rollbacks for a workspace."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.metadata_file = root / ".termicode" / "checkpoints.json"

    def is_repo(self) -> bool:
        """Return True if workspace is a Git repository."""
        return is_git_repo(self.root)

    def ensure_git_repo(self) -> None:
        """Raise GitRepositoryError if workspace is not a Git repository."""
        if not self.is_repo():
            raise GitRepositoryError(
                "Workspace is not a Git repository. "
                "Run 'termicode init' to initialize version control."
            )

    def ensure_head_commit(self) -> str:
        """Ensure at least one commit exists in the repo and return its hash."""
        self.ensure_git_repo()
        head = get_head_commit(self.root)
        if head is not None:
            return head

        run_git(["commit", "--allow-empty", "-m", "Initial commit by TermiCode"], self.root)
        head = get_head_commit(self.root)
        if head is None:
            raise GitError("Failed to establish Git HEAD commit.")
        return head

    def has_uncommitted_changes(self) -> bool:
        """Check if workspace currently has uncommitted changes."""
        if not self.is_repo():
            return False
        return has_uncommitted_changes(self.root)

    def get_uncommitted_changes(self) -> list[str]:
        """Return list of modified or untracked files."""
        if not self.is_repo():
            return []
        return get_uncommitted_changes(self.root)

    def _load_metadata(self) -> list[GitCheckpoint]:
        if not self.metadata_file.exists():
            return []
        try:
            raw = json.loads(self.metadata_file.read_text(encoding="utf-8"))
            items = raw.get("checkpoints", []) if isinstance(raw, dict) else raw
            return [GitCheckpoint(**item) for item in items]
        except Exception:
            return []

    def _save_metadata(self, cps: list[GitCheckpoint]) -> None:
        self.metadata_file.parent.mkdir(parents=True, exist_ok=True)
        data = {"checkpoints": [cp.model_dump() for cp in cps]}
        self.metadata_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def create_checkpoint(
        self,
        message: str | None = None,
        checkpoint_type: str = "manual",
    ) -> GitCheckpoint:
        """Save a new checkpoint using git stash / commit hash snapshot."""
        self.ensure_git_repo()
        head_hash = self.ensure_head_commit()

        cp_id = f"cp_{uuid.uuid4().hex[:8]}"
        desc = message or f"{checkpoint_type.capitalize()} checkpoint {cp_id}"
        uncommitted = self.get_uncommitted_changes()

        has_stash = False
        stash_oid: str | None = None

        if uncommitted:
            res = run_git(
                ["stash", "push", "-u", "-m", f"TermiCode checkpoint: {cp_id} - {desc}"], self.root
            )
            if "Saved working directory" in res.stdout:
                has_stash = True
                stash_oid = run_git(["rev-parse", "stash@{0}"], self.root).stdout.strip()
                run_git(["stash", "apply", "stash@{0}"], self.root)

        checkpoint = GitCheckpoint(
            id=cp_id,
            timestamp=datetime.now(UTC).isoformat(),
            message=desc,
            commit_hash=head_hash,
            has_stash=has_stash,
            stash_oid=stash_oid,
            type=checkpoint_type,
            files=uncommitted,
        )

        cps = self._load_metadata()
        cps.append(checkpoint)
        self._save_metadata(cps)
        return checkpoint

    def list_checkpoints(self, limit: int = 10) -> list[GitCheckpoint]:
        """Return list of recorded checkpoints ordered newest first."""
        self.ensure_git_repo()
        cps = self._load_metadata()
        cps.reverse()
        return cps[:limit]

    def get_checkpoint(self, checkpoint_id: str | None = None) -> GitCheckpoint | None:
        """Retrieve a specific checkpoint by ID, or the latest checkpoint if ID is None."""
        self.ensure_git_repo()
        cps = self._load_metadata()
        if not cps:
            return None
        if checkpoint_id is None:
            return cps[-1]
        for cp in reversed(cps):
            if cp.id == checkpoint_id or cp.id.startswith(checkpoint_id):
                return cp
        return None

    def rollback(self, checkpoint_id: str | None = None) -> GitCheckpoint:
        """Revert the workspace to the specified checkpoint (or latest checkpoint / HEAD)."""
        self.ensure_git_repo()
        target = self.get_checkpoint(checkpoint_id)

        if target is None:
            if checkpoint_id is not None:
                raise CheckpointNotFoundError(f"Checkpoint '{checkpoint_id}' not found.")
            head_hash = self.ensure_head_commit()
            run_git(["reset", "--hard", "HEAD"], self.root)
            run_git(["clean", "-fd", "-e", ".termicode"], self.root)
            return GitCheckpoint(
                id="HEAD",
                timestamp=datetime.now(UTC).isoformat(),
                message="Workspace restored to HEAD",
                commit_hash=head_hash,
                has_stash=False,
            )

        # 1. Reset tracked files to the checkpoint's commit
        run_git(["reset", "--hard", target.commit_hash], self.root)

        # 2. Clean untracked files (preserving .termicode)
        run_git(["clean", "-fd", "-e", ".termicode"], self.root)

        # 3. If checkpoint captured uncommitted changes, apply the exact stash commit
        if target.has_stash and target.stash_oid:
            run_git(["stash", "apply", target.stash_oid], self.root)

        return target

    def commit_changes(self, message: str) -> str:
        """Stage all workspace modifications and create a Git commit."""
        self.ensure_git_repo()
        self.ensure_head_commit()
        run_git(["add", "-A"], self.root)
        run_git(["commit", "-m", message], self.root)
        head = get_head_commit(self.root)
        if head is None:
            raise GitError("Failed to obtain commit hash after commit.")
        return head
