"""Workspace safety boundary and safe path resolution.

The LLM proposes actions, but the deterministic workspace boundary
strictly enforces that all file accesses remain inside the approved root directory
and prevents disclosure of sensitive files such as .env, keys, and credentials.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

# Directories blocked by default for security and privacy
PROTECTED_DIR_NAMES: Final[frozenset[str]] = frozenset(
    {
        ".git",
        ".ssh",
        ".aws",
        ".gnupg",
    }
)

# Specific file patterns/names blocked by default
PROTECTED_EXACT_NAMES: Final[frozenset[str]] = frozenset(
    {
        ".env",
        "id_rsa",
        "id_ed25519",
        "id_ecdsa",
        "id_dsa",
        "credentials",
        "credentials.json",
    }
)

PROTECTED_SUFFIXES: Final[frozenset[str]] = frozenset(
    {
        ".pem",
        ".key",
    }
)


class WorkspaceError(Exception):
    """Base exception for workspace security and boundary violations."""


class WorkspaceNotFoundError(WorkspaceError):
    """Raised when the requested workspace directory does not exist."""


class WorkspaceNotADirectoryError(WorkspaceError):
    """Raised when the specified workspace path is a file rather than a directory."""


class WorkspaceEscapeError(WorkspaceError):
    """Raised when a path attempts to escape the approved workspace root."""


class SensitivePathError(WorkspaceError):
    """Raised when a path targets sensitive credentials or protected system files."""


class WorkspaceBoundary:
    """Enforces deterministic isolation to an approved root workspace directory."""

    def __init__(self, root: str | Path) -> None:
        raw_path = Path(root)
        resolved_root = raw_path.resolve()

        if not resolved_root.exists():
            raise WorkspaceNotFoundError(f"Workspace directory does not exist: {raw_path}")

        if not resolved_root.is_dir():
            raise WorkspaceNotADirectoryError(f"Workspace path is not a directory: {raw_path}")

        self._root = resolved_root

    @property
    def root(self) -> Path:
        """The absolute, canonical workspace root directory."""
        return self._root

    @property
    def termicode_dir(self) -> Path:
        """Path to the local .termicode metadata and transcript directory."""
        return self._root / ".termicode"

    def ensure_termicode_dir(self) -> Path:
        """Create the .termicode directory if it does not already exist."""
        path = self.termicode_dir
        path.mkdir(parents=True, exist_ok=True)
        return path

    def is_sensitive(self, path: Path) -> bool:
        """Check whether a path within workspace targets sensitive files or directories."""
        try:
            rel = path.relative_to(self._root)
        except ValueError:
            return True

        for part in rel.parts:
            # Block protected directories
            if part in PROTECTED_DIR_NAMES:
                return True

        filename = path.name.lower()

        # Block exact sensitive names
        if filename in PROTECTED_EXACT_NAMES:
            return True

        # Block .env.* files (e.g. .env.local, .env.prod), but allow .env.example
        if filename.startswith(".env") and filename != ".env.example":
            return True

        # Block private keys and certificates
        if path.suffix.lower() in PROTECTED_SUFFIXES:
            return True

        return False

    def resolve_safe(self, target: str | Path, allow_sensitive: bool = False) -> Path:
        """Resolve a target path securely against the workspace root.

        Args:
            target: Relative or absolute path to resolve.
            allow_sensitive: If True, bypasses sensitive-file blocking (defaults to False).

        Returns:
            The resolved absolute Path strictly inside the workspace root.

        Raises:
            WorkspaceEscapeError: If the resolved path escapes the workspace root.
            SensitivePathError: If the resolved path matches sensitive patterns
                and allow_sensitive is False.
        """
        raw = Path(target)

        # If target is relative, anchor it to workspace root; otherwise resolve absolute
        if raw.is_absolute():
            resolved = raw.resolve()
        else:
            resolved = (self._root / raw).resolve()

        # Verify containment under workspace root
        if not resolved.is_relative_to(self._root):
            raise WorkspaceEscapeError(
                f"Path traversal detected: '{target}' resolves to '{resolved}', "
                f"which is outside approved workspace root '{self._root}'."
            )

        # Verify sensitive file protection
        if not allow_sensitive and self.is_sensitive(resolved):
            raise SensitivePathError(
                f"Access denied: '{target}' points to a protected or sensitive path."
            )

        return resolved

    def is_safe(self, target: str | Path, allow_sensitive: bool = False) -> bool:
        """Check whether a target path is safely contained and permitted."""
        try:
            self.resolve_safe(target, allow_sensitive=allow_sensitive)
            return True
        except WorkspaceError:
            return False
