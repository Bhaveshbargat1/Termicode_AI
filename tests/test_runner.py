"""Unit tests for secure command runner, policy enforcement, timeouts, and auditing."""

from pathlib import Path

import pytest

from termicode.runner import (
    CommandResult,
    CommandRunner,
    DisallowedExecutableError,
    UnsafeArgumentError,
    validate_command,
)
from termicode.tools.base import ToolContext
from termicode.tools.command import RunCommandTool
from termicode.workspace import WorkspaceBoundary

# --- Policy Validator Tests ---


def test_validate_command_permitted_pytest() -> None:
    cmd = validate_command(["pytest", "-q", "tests/test_cli.py"])
    assert cmd == ["pytest", "-q", "tests/test_cli.py"]


def test_validate_command_permitted_git_status() -> None:
    cmd = validate_command(["git", "status", "--short"])
    assert cmd == ["git", "status", "--short"]


def test_validate_command_permitted_python_module() -> None:
    cmd = validate_command(["python", "-m", "pytest", "-v"])
    assert cmd == ["python", "-m", "pytest", "-v"]


def test_validate_command_rejects_empty_argv() -> None:
    with pytest.raises(UnsafeArgumentError, match="cannot be empty"):
        validate_command([])


def test_validate_command_rejects_non_string_elements() -> None:
    with pytest.raises(TypeError, match="must be strings"):
        validate_command(["pytest", 123])  # type: ignore[list-item]


@pytest.mark.parametrize(
    "prohibited_cmd",
    [
        ["bash", "-c", "echo 1"],
        ["sh", "script.sh"],
        ["cmd.exe", "/c", "dir"],
        ["powershell", "-Command", "Get-Process"],
        ["curl", "https://example.com"],
        ["wget", "https://example.com"],
        ["rm", "-rf", "/"],
        ["del", "file.txt"],
        ["sudo", "pytest"],
        ["runas", "/user:admin", "cmd"],
    ],
)
def test_validate_command_rejects_unallowlisted_executables(prohibited_cmd: list[str]) -> None:
    with pytest.raises(DisallowedExecutableError):
        validate_command(prohibited_cmd)


@pytest.mark.parametrize(
    "metachar_cmd",
    [
        ["pytest", "|", "grep", "test"],
        ["pytest", "tests | rm -rf"],
        ["pytest", ">", "out.txt"],
        ["pytest", "tests > /tmp/out"],
        ["pytest", "<", "in.txt"],
        ["pytest", ";", "git", "status"],
        ["pytest", "tests; rm -rf"],
        ["pytest", "&&", "git", "diff"],
        ["pytest", "||", "echo", "fail"],
        ["python", "-c", "$(cat /etc/passwd)"],
        ["python", "`id`"],
        ["pytest", "tests\nrm -rf /"],
        ["pytest", "tests\recho hi"],
    ],
)
def test_validate_command_rejects_shell_metacharacters(metachar_cmd: list[str]) -> None:
    with pytest.raises(UnsafeArgumentError, match="Prohibited shell metacharacter"):
        validate_command(metachar_cmd)


def test_validate_command_rejects_environment_mutation() -> None:
    with pytest.raises(UnsafeArgumentError, match="Inline environment mutation"):
        validate_command(["PYTHONPATH=./src", "pytest"])


@pytest.mark.parametrize(
    "pip_cmd",
    [
        ["pip", "install", "requests"],
        ["pip3", "install", "requests"],
        ["python", "-m", "pip", "install", "malicious-pkg"],
        ["python", "-m", "pip", "uninstall", "pytest"],
    ],
)
def test_validate_command_rejects_package_installation(pip_cmd: list[str]) -> None:
    with pytest.raises((UnsafeArgumentError, DisallowedExecutableError)):
        validate_command(pip_cmd)


def test_validate_command_rejects_python_inline_code() -> None:
    with pytest.raises(UnsafeArgumentError, match="Arbitrary inline execution via 'python -c'"):
        validate_command(["python", "-c", "print('hello')"])


@pytest.mark.parametrize(
    "git_cmd",
    [
        ["git", "push", "origin", "main"],
        ["git", "reset", "--hard", "HEAD~1"],
        ["git", "checkout", "."],
        ["git", "commit", "-m", "auto"],
        ["git", "clean", "-fdx"],
        ["git", "rm", "file.py"],
        ["git", "rebase", "main"],
        ["git", "merge", "dev"],
        ["git", "pull"],
    ],
)
def test_validate_command_rejects_prohibited_git_operations(git_cmd: list[str]) -> None:
    with pytest.raises(UnsafeArgumentError, match="Disallowed git subcommand"):
        validate_command(git_cmd)


def test_validate_command_rejects_workspace_path_escape(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    with pytest.raises(UnsafeArgumentError, match="escapes the workspace boundary"):
        validate_command(["pytest", "../../outside_tests.py"], workspace=boundary)


# --- Subprocess Command Runner Execution Tests ---


def test_command_runner_executes_permitted_pytest(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    result = CommandRunner.run(
        argv=["pytest", "--version"],
        cwd=boundary.root,
        workspace=boundary,
    )
    assert isinstance(result, CommandResult)
    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.truncated is False
    assert "pytest" in (result.stdout + result.stderr).lower()
    assert result.audit["status"] == "success"
    assert result.audit["duration_ms"] > 0


def test_command_runner_timeout_enforcement(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    # Run a slow Python script that exceeds timeout
    slow_script = tmp_path / "slow.py"
    slow_script.write_text("import time; time.sleep(5)\n", encoding="utf-8")

    result = CommandRunner.run(
        argv=["python", "slow.py"],
        cwd=boundary.root,
        workspace=boundary,
        timeout_seconds=1,
    )
    assert result.timed_out is True
    assert result.audit["status"] == "timed_out"


def test_command_runner_output_capping(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    # Script generating 100 KB of stdout
    spam_script = tmp_path / "spam.py"
    spam_script.write_text("print('X' * 50000)\n", encoding="utf-8")

    result = CommandRunner.run(
        argv=["python", "spam.py"],
        cwd=boundary.root,
        workspace=boundary,
        max_output_bytes=1000,
    )
    assert result.truncated is True
    assert "Warning: stdout truncated at 1,000 bytes cap" in result.stdout
    assert len(result.stdout) < 2000


def test_command_runner_forces_resolved_cwd(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    outside_dir = tmp_path.parent / "outside_dir"
    outside_dir.mkdir(exist_ok=True)

    with pytest.raises(ValueError, match="escapes workspace root"):
        CommandRunner.run(
            argv=["pytest", "--version"],
            cwd=outside_dir,
            workspace=boundary,
        )


# --- RunCommandTool and Approval Workflow Tests ---


def test_run_command_tool_unconfirmed_requires_approval(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    tool = RunCommandTool()
    ctx = ToolContext(workspace=boundary, user_confirmed=False)

    result = tool.run(
        {"command": ["pytest", "--version"]},
        context=ctx,
    )

    assert result.ok is True
    assert "Pending user approval to execute command:" in result.output
    assert "pytest --version" in result.output
    assert result.changed_files == []
    assert result.metadata["pending_approval"] is True


def test_run_command_tool_confirmed_executes(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    tool = RunCommandTool()
    ctx = ToolContext(workspace=boundary, user_confirmed=True)

    result = tool.run(
        {"command": ["pytest", "--version"]},
        context=ctx,
    )

    assert result.ok is True
    assert "pytest" in result.output.lower()
    assert result.metadata["applied"] is True
    assert result.metadata["exit_code"] == 0
    assert result.metadata["timed_out"] is False
    assert "command_audit" in result.metadata
    assert result.metadata["command_audit"]["status"] == "success"


def test_run_command_tool_rejects_unsafe_command_even_if_confirmed(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    tool = RunCommandTool()
    ctx = ToolContext(workspace=boundary, user_confirmed=True)

    result = tool.run(
        {"command": ["pip", "install", "malicious"]},
        context=ctx,
    )

    assert result.ok is False
    assert "Command policy violation" in (result.error or "")
    assert result.metadata.get("applied") is not True
