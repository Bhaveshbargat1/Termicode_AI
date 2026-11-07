"""Subprocess execution engine enforcing timeouts, output caps, and auditing."""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, Field

from termicode.runner.validator import _get_base_executable_name, validate_command
from termicode.workspace import WorkspaceBoundary

DEFAULT_TIMEOUT_SECONDS: Final[int] = 30
DEFAULT_MAX_OUTPUT_BYTES: Final[int] = 65536  # 64 KB


class CommandResult(BaseModel):
    """Structured result from a subprocess command execution."""

    command: list[str] = Field(description="Command and arguments executed.")
    cwd: str = Field(description="Working directory the command ran in.")
    exit_code: int | None = Field(description="Exit return code from the process.")
    stdout: str = Field(default="", description="Captured standard output.")
    stderr: str = Field(default="", description="Captured standard error.")
    timed_out: bool = Field(
        default=False, description="Whether execution was terminated due to timeout."
    )
    duration_ms: float = Field(default=0.0, description="Execution duration in milliseconds.")
    truncated: bool = Field(
        default=False, description="Whether output exceeded maximum capture capacity."
    )
    audit: dict[str, Any] = Field(default_factory=dict, description="Structured audit log entry.")


def resolve_executable_path(command_name: str) -> Path | str:
    """Resolve an allowlisted command to an executable path.

    Checks virtual environment Scripts/bin first to ensure deterministic runtime tooling,
    then PATH via shutil.which.
    """
    raw_name = command_name.strip()
    base_name = _get_base_executable_name(raw_name)

    # 1. Check inside active Python virtual environment (sys.executable parent)
    venv_dir = Path(sys.executable).parent
    candidates: list[str] = []
    if sys.platform == "win32":
        candidates.extend([f"{base_name}.exe", f"{base_name}.cmd", f"{base_name}.bat", base_name])
    else:
        candidates.append(base_name)

    for cand in candidates:
        target = venv_dir / cand
        if target.is_file():
            return target

    # 2. Check system PATH via shutil.which
    found = shutil.which(raw_name)
    if found:
        return Path(found)

    # 3. Fallback to raw command name
    return raw_name


class CommandRunner:
    """Subprocess command runner strictly constrained to workspace cwd and allowlist."""

    @classmethod
    def run(
        cls,
        argv: Sequence[str],
        cwd: Path,
        workspace: WorkspaceBoundary | None = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    ) -> CommandResult:
        """Execute a validated command array without shell and with hard boundaries.

        Args:
            argv: Argument vector to run.
            cwd: Working directory (must be workspace root).
            workspace: Boundary for path traversal verification.
            timeout_seconds: Maximum wall-clock execution time before SIGKILL.
            max_output_bytes: Output cap for stdout and stderr.

        Returns:
            Structured CommandResult with execution telemetry and audit log.
        """
        # Deterministically validate command against safety policy
        validated_argv = validate_command(argv, workspace=workspace)

        # Force resolved working directory
        resolved_cwd = Path(cwd).resolve()
        if workspace is not None and not resolved_cwd.is_relative_to(workspace.root):
            raise ValueError(f"Command working directory '{resolved_cwd}' escapes workspace root.")

        # Resolve executable binary
        resolved_exe = resolve_executable_path(validated_argv[0])
        executable_argv = [str(resolved_exe)] + validated_argv[1:]

        started_at = datetime.now(UTC).isoformat()
        start_mono = time.perf_counter()

        timed_out = False
        truncated = False
        raw_stdout = b""
        raw_stderr = b""
        exit_code: int | None = None

        try:
            # subprocess with shell=False and argument arrays only
            proc = subprocess.Popen(
                executable_argv,
                cwd=str(resolved_cwd),
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            raw_stdout, raw_stderr = proc.communicate(timeout=timeout_seconds)
            exit_code = proc.returncode

        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
            try:
                raw_stdout, raw_stderr = proc.communicate(timeout=5)
            except Exception:
                pass
            exit_code = proc.returncode

        except OSError as err:
            exit_code = 127
            raw_stderr = f"Command execution failed: {err}".encode()

        duration_ms = round((time.perf_counter() - start_mono) * 1000, 3)
        finished_at = datetime.now(UTC).isoformat()

        # Enforce output capping
        if len(raw_stdout) > max_output_bytes:
            truncated = True
            stdout = raw_stdout[:max_output_bytes].decode("utf-8", errors="replace")
            stdout += f"\n\n[Warning: stdout truncated at {max_output_bytes:,} bytes cap]"
        else:
            stdout = raw_stdout.decode("utf-8", errors="replace")

        if len(raw_stderr) > max_output_bytes:
            truncated = True
            stderr = raw_stderr[:max_output_bytes].decode("utf-8", errors="replace")
            stderr += f"\n\n[Warning: stderr truncated at {max_output_bytes:,} bytes cap]"
        else:
            stderr = raw_stderr.decode("utf-8", errors="replace")

        # Compile structured audit entry
        audit_entry = {
            "command": validated_argv,
            "executable_path": str(resolved_exe),
            "cwd": str(resolved_cwd),
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_ms": duration_ms,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "truncated": truncated,
            "status": "timed_out" if timed_out else ("success" if exit_code == 0 else "failed"),
        }

        return CommandResult(
            command=validated_argv,
            cwd=str(resolved_cwd),
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            duration_ms=duration_ms,
            truncated=truncated,
            audit=audit_entry,
        )
