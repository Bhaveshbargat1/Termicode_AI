"""Secure command execution tool for allowlisted subprocess operations."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from termicode.runner.runner import CommandRunner
from termicode.runner.validator import CommandSecurityError, validate_command
from termicode.tools.base import (
    BaseTool,
    RiskLevel,
    ToolContext,
    ToolResult,
)
from termicode.workspace import WorkspaceBoundary


def _get_boundary(context: ToolContext | None) -> WorkspaceBoundary:
    """Extract WorkspaceBoundary from context or fallback to current directory."""
    if context and context.workspace is not None:
        return context.workspace
    return WorkspaceBoundary(Path.cwd())


class RunCommandArgs(BaseModel):
    command: list[str] = Field(
        description="Command line arguments array, e.g. ['pytest', '-q', 'tests/test_cli.py'].",
    )
    timeout_seconds: int = Field(
        default=30,
        ge=1,
        le=300,
        description="Maximum execution timeout in seconds.",
    )


class RunCommandTool(BaseTool[RunCommandArgs]):
    name = "run_command"
    description = (
        "Execute an allowlisted command (pytest, python, ruff, mypy, git) strictly "
        "inside the workspace. Requires explicit user approval before running."
    )
    risk_level = RiskLevel.EXECUTE
    args_schema = RunCommandArgs

    def _execute(self, args: RunCommandArgs, context: ToolContext | None = None) -> ToolResult:
        boundary = _get_boundary(context)

        # Deterministically validate command against security allowlists
        try:
            validated_argv = validate_command(args.command, workspace=boundary)
        except CommandSecurityError as err:
            return ToolResult(
                ok=False,
                error=f"Command policy violation: {err}",
            )

        cmd_display = " ".join(validated_argv)
        is_confirmed = bool(context and context.user_confirmed)

        # Gated by user approval
        if not is_confirmed:
            return ToolResult(
                ok=True,
                output=(
                    f"Pending user approval to execute command:\n"
                    f"  Command: {cmd_display}\n"
                    f"  Working directory: {boundary.root}\n\n"
                    "Confirmation required to execute."
                ),
                changed_files=[],
                metadata={
                    "pending_approval": True,
                    "action": "run_command",
                    "command": validated_argv,
                    "cwd": str(boundary.root),
                },
            )

        # Approved: execute subprocess safely
        result = CommandRunner.run(
            argv=validated_argv,
            cwd=boundary.root,
            workspace=boundary,
            timeout_seconds=args.timeout_seconds,
        )

        ok = (result.exit_code == 0) and not result.timed_out
        error_msg = None
        if result.timed_out:
            error_msg = f"Command timed out after {args.timeout_seconds} seconds."
        elif result.exit_code != 0:
            error_msg = f"Command exited with non-zero status code: {result.exit_code}"

        output_parts: list[str] = []
        if result.stdout.strip():
            output_parts.append(f"[stdout]\n{result.stdout.strip()}")
        if result.stderr.strip():
            output_parts.append(f"[stderr]\n{result.stderr.strip()}")
        formatted_output = "\n\n".join(output_parts) if output_parts else "(No output produced)"

        return ToolResult(
            ok=ok,
            output=formatted_output,
            error=error_msg,
            changed_files=[],
            metadata={
                "applied": True,
                "action": "run_command",
                "command": validated_argv,
                "cwd": str(boundary.root),
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "duration_ms": result.duration_ms,
                "truncated": result.truncated,
                "command_audit": result.audit,
            },
        )
