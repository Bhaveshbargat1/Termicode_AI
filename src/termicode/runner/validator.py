"""Security validator for allowlisted subprocess commands."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Final

from termicode.workspace import WorkspaceBoundary, WorkspaceEscapeError

# Allowlisted base executables permitted in TermiCode AI MVP
ALLOWED_EXECUTABLES: Final[frozenset[str]] = frozenset(
    {
        "python",
        "pytest",
        "ruff",
        "mypy",
        "git",
    }
)

# Read-only git subcommands permitted in MVP
ALLOWED_GIT_SUBCOMMANDS: Final[frozenset[str]] = frozenset(
    {
        "status",
        "diff",
        "log",
        "branch",
        "show",
    }
)

# Explicitly prohibited git subcommands
PROHIBITED_GIT_SUBCOMMANDS: Final[frozenset[str]] = frozenset(
    {
        "push",
        "reset",
        "checkout",
        "commit",
        "clean",
        "rm",
        "rebase",
        "merge",
        "pull",
        "clone",
        "fetch",
        "config",
        "remote",
    }
)

# Permitted python -m modules
ALLOWED_PYTHON_MODULES: Final[frozenset[str]] = frozenset(
    {
        "pytest",
        "unittest",
        "ruff",
        "mypy",
    }
)

# Prohibited shell tokens and metacharacters
PROHIBITED_SHELL_PATTERNS: Final[tuple[str, ...]] = (
    "|",
    ">",
    "<",
    ";",
    "&&",
    "||",
    "`",
    "$(",
    "${",
    "\n",
    "\r",
)

# Prohibited privilege / destructive command names
PROHIBITED_COMMAND_NAMES: Final[frozenset[str]] = frozenset(
    {
        "sudo",
        "su",
        "runas",
        "doas",
        "pkexec",
        "rm",
        "del",
        "rmdir",
        "format",
        "erase",
        "dd",
        "mkfs",
        "chmod",
        "chown",
        "curl",
        "wget",
        "bash",
        "sh",
        "zsh",
        "cmd",
        "powershell",
        "pwsh",
        "pip",
        "pip3",
        "npm",
        "yarn",
        "pnpm",
    }
)


class CommandSecurityError(Exception):
    """Base exception for command security violations."""


class DisallowedExecutableError(CommandSecurityError):
    """Raised when an executable is not in the MVP allowlist."""


class UnsafeArgumentError(CommandSecurityError):
    """Raised when an argument contains prohibited shell tokens, subcommands, or escapes."""


def _get_base_executable_name(raw_exe: str) -> str:
    """Extract lowercase base command name, stripping file extensions and path prefixes."""
    name = Path(raw_exe).name.lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return name


def validate_command(
    argv: Sequence[str],
    workspace: WorkspaceBoundary | None = None,
) -> list[str]:
    """Validate a command argument vector against deterministic security policies.

    Args:
        argv: Command line arguments as a list/sequence of strings.
        workspace: Optional workspace boundary for verifying path arguments.

    Returns:
        The validated argv as a list of strings.

    Raises:
        TypeError: If argv or any argument is not a string.
        UnsafeArgumentError: If prohibited shell metacharacters or subcommands are found.
        DisallowedExecutableError: If the executable is not in the allowlist.
    """
    if not argv:
        raise UnsafeArgumentError("Command argument array cannot be empty.")

    for idx, arg in enumerate(argv):
        if not isinstance(arg, str):
            raise TypeError(
                f"All command arguments must be strings; "
                f"argument at index {idx} is {type(arg).__name__}"
            )
        if not arg.strip() and idx == 0:
            raise UnsafeArgumentError("Executable command name cannot be blank.")

        # Check for shell metacharacters, pipes, redirects, chaining, substitutions
        for pattern in PROHIBITED_SHELL_PATTERNS:
            if pattern in arg:
                raise UnsafeArgumentError(
                    f"Prohibited shell metacharacter '{pattern}' detected in argument '{arg}'."
                )

        # Check for environment mutation syntax (e.g. 'FOO=bar cmd')
        if idx == 0 and "=" in arg and not arg.startswith(("-", "/")):
            raise UnsafeArgumentError(
                f"Inline environment mutation '{arg}' is prohibited in command invocations."
            )

    # 1. Check executable against allowlist
    raw_exe = argv[0].strip()
    base_exe = _get_base_executable_name(raw_exe)

    if base_exe in PROHIBITED_COMMAND_NAMES:
        raise DisallowedExecutableError(
            f"Command '{base_exe}' is explicitly prohibited by security policy."
        )

    if base_exe not in ALLOWED_EXECUTABLES:
        raise DisallowedExecutableError(
            f"Executable '{raw_exe}' is not in the allowlist. "
            f"Allowed executables: {sorted(ALLOWED_EXECUTABLES)}"
        )

    # 2. Package install checks (pip, pip install) across all arguments
    for arg in argv[1:]:
        if arg.lower() in ("pip", "pip3", "install"):
            if "pip" in [a.lower() for a in argv]:
                raise UnsafeArgumentError("Package installation commands are prohibited in MVP.")

    # 3. Tool-specific constraints
    if base_exe == "git":
        # Extract first non-flag argument as git subcommand
        subcommand: str | None = None
        for arg in argv[1:]:
            if not arg.startswith("-"):
                subcommand = arg.lower()
                break

        if subcommand is None:
            # e.g. 'git --version'
            pass
        elif subcommand in PROHIBITED_GIT_SUBCOMMANDS:
            raise UnsafeArgumentError(
                f"Disallowed git subcommand '{subcommand}'. "
                "MVP permits read-only git operations only (status, diff, log, branch, show)."
            )
        elif subcommand not in ALLOWED_GIT_SUBCOMMANDS:
            raise UnsafeArgumentError(
                f"Git subcommand '{subcommand}' is not allowlisted. "
                f"Allowed git subcommands: {sorted(ALLOWED_GIT_SUBCOMMANDS)}"
            )

    elif base_exe == "python":
        args_lower = [a.lower() for a in argv[1:]]

        # Block inline python -c execution
        if "-c" in args_lower:
            raise UnsafeArgumentError(
                "Arbitrary inline execution via 'python -c' is prohibited in MVP."
            )

        # Check python -m module invocation
        all_lower = [a.lower() for a in argv]
        if "-m" in all_lower:
            m_idx = all_lower.index("-m")
            if m_idx + 1 >= len(argv):
                raise UnsafeArgumentError("Missing module name following '-m' flag.")
            module_name = argv[m_idx + 1].lower()
            if module_name == "pip":
                raise UnsafeArgumentError(
                    "Package management via 'python -m pip' is prohibited in MVP."
                )
            if module_name not in ALLOWED_PYTHON_MODULES:
                raise UnsafeArgumentError(
                    f"Module '{module_name}' is not allowlisted for 'python -m'. "
                    f"Allowed modules: {sorted(ALLOWED_PYTHON_MODULES)}"
                )

    # 4. Path argument containment check (reject traversal escaping workspace)
    if workspace is not None:
        for arg in argv[1:]:
            # If argument contains path traversal indicators
            if ".." in arg:
                try:
                    workspace.resolve_safe(arg)
                except WorkspaceEscapeError as err:
                    raise UnsafeArgumentError(
                        f"Path argument '{arg}' escapes the workspace boundary: {err}"
                    ) from err

    return list(argv)
