"""Tests for Typer CLI interface, workflow options, commands, and policy enforcement."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from termicode import __version__
from termicode.cli import app
from termicode.providers.base import CompletionResponse, ToolCall
from termicode.providers.fake import FakeProvider

runner = CliRunner()


def test_cli_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "TermiCode AI" in result.output
    assert "status" in result.output
    assert "workflows" in result.output
    assert "run" in result.output
    assert "--workspace" in result.output


def test_cli_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert f"TermiCode AI v{__version__}" in result.output


def test_cli_status_default_workspace() -> None:
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "TermiCode AI -- Runtime Status" in result.output
    assert "Workspace Root" in result.output
    assert "Valid & Contained" in result.output


def test_cli_status_custom_workspace(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--workspace", str(tmp_path), "status"])
    assert result.exit_code == 0
    assert "test_cli_status_custom" in result.output
    assert "Valid & Contained" in result.output


def test_cli_status_invalid_workspace() -> None:
    invalid_path = Path("/non_existent_directory_for_termicode_tests_99999")
    result = runner.invoke(app, ["--workspace", str(invalid_path), "status"])
    assert result.exit_code == 1
    assert "Workspace Validation Error" in result.output


def test_cli_status_help() -> None:
    result = runner.invoke(app, ["status", "--help"])
    assert result.exit_code == 0
    assert "Display active workspace validation" in result.output


def test_cli_workflows_command() -> None:
    """Verify `termicode workflows` displays all six workflow profiles in a table."""
    result = runner.invoke(app, ["workflows"])
    assert result.exit_code == 0
    assert "Available TermiCode Workflow Profiles" in result.output
    for wf in ["generate", "debug", "understand", "tests", "review", "refactor"]:
        assert wf in result.output
    assert "Read-Only" in result.output
    assert "Read / Write" in result.output
    assert "Execute" in result.output


def test_cli_run_help() -> None:
    """Verify `termicode run --help` exposes workflow selection and budget flags."""
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    assert "--workflow" in result.output or "-W" in result.output
    assert "--max-steps" in result.output or "-s" in result.output
    assert "--yes" in result.output or "-y" in result.output


def test_cli_run_unknown_workflow(tmp_path: Path) -> None:
    """Verify running with an unknown workflow fails gracefully with clear error."""
    result = runner.invoke(
        app,
        ["--workspace", str(tmp_path), "run", "do something", "--workflow", "nonexistent_wf"],
    )
    assert result.exit_code == 1
    assert "Workflow Configuration Error" in result.output
    assert "Unknown workflow profile 'nonexistent_wf'" in result.output


def test_cli_run_missing_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify running without an API key produces clear authentication error."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TERMICODE_API_KEY", raising=False)
    result = runner.invoke(
        app,
        ["--workspace", str(tmp_path), "run", "explain main.py", "--workflow", "understand"],
    )
    assert result.exit_code == 1
    assert "Provider Authentication Error" in result.output


def test_cli_dedicated_workflow_commands_help() -> None:
    """Verify dedicated workflow shortcut commands have clear help text."""
    for cmd in ["understand", "review", "generate", "debug", "tests", "refactor"]:
        result = runner.invoke(app, [cmd, "--help"])
        assert result.exit_code == 0
        assert f"Usage: termicode {cmd}" in result.output


def test_cli_run_successful_execution(tmp_path: Path) -> None:
    """Verify `termicode run` completes successfully using a mock provider."""
    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                content="Codebase structure explanation: Clean src/ layout.",
                finish_reason="stop",
            )
        ]
    )

    with patch("termicode.cli.OpenAIProvider.from_env", return_value=fake_provider):
        result = runner.invoke(
            app,
            [
                "--workspace",
                str(tmp_path),
                "run",
                "explain architecture",
                "--workflow",
                "understand",
            ],
        )

    assert result.exit_code == 0
    assert "Execution Summary" in result.output
    assert "understand" in result.output
    assert "completed" in result.output
    assert "Clean src/ layout" in result.output


def test_cli_shortcut_understand_execution(tmp_path: Path) -> None:
    """Verify `termicode understand` shortcut executes properly."""
    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                content="Everything looks great in understand mode.",
                finish_reason="stop",
            )
        ]
    )

    with patch("termicode.cli.OpenAIProvider.from_env", return_value=fake_provider):
        result = runner.invoke(
            app,
            ["--workspace", str(tmp_path), "understand", "explain everything"],
        )

    assert result.exit_code == 0
    assert "Execution Summary" in result.output
    assert "understand" in result.output
    assert "completed" in result.output


def test_cli_run_policy_rejection_exits_code_1(tmp_path: Path) -> None:
    """Verify that a policy rejection during CLI run exits with code 1."""
    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="write_file",
                        arguments={"path": "forbidden.txt", "content": "bad"},
                    )
                ],
                finish_reason="tool_calls",
            )
        ]
    )

    with patch("termicode.cli.OpenAIProvider.from_env", return_value=fake_provider):
        result = runner.invoke(
            app,
            [
                "--workspace",
                str(tmp_path),
                "run",
                "attempt unauthorized write",
                "--workflow",
                "understand",
            ],
        )

    assert result.exit_code == 1
    assert "policy_rejected" in result.output


def test_cli_approval_prompt_shows_risk_path_and_diff_on_approve(tmp_path: Path) -> None:
    """Verify approval prompt renders risk, workspace-relative path, diff, and executes on 'y'."""
    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="write_file",
                        arguments={
                            "path": "src/feature.py",
                            "content": "def feature():\n    return 42\n",
                        },
                    )
                ],
                finish_reason="tool_calls",
            ),
            CompletionResponse(
                content="Feature implementation completed.",
                finish_reason="stop",
            ),
        ]
    )

    with patch("termicode.cli.OpenAIProvider.from_env", return_value=fake_provider):
        result = runner.invoke(
            app,
            [
                "--workspace",
                str(tmp_path),
                "run",
                "create feature.py",
                "--workflow",
                "generate",
            ],
            input="y\n",
        )

    assert result.exit_code == 0
    # Prompt readable contents
    assert "Action Approval Required: write_file" in result.output
    assert "Risk Level:" in result.output
    assert "WRITE" in result.output
    assert "Target File:" in result.output
    assert "src/feature.py" in result.output
    assert "(workspace-relative)" in result.output
    assert "Diff / Preview:" in result.output
    assert "+def feature():" in result.output
    assert "Action approved." in result.output

    # File was actually written to disk
    target_file = tmp_path / "src" / "feature.py"
    assert target_file.exists()
    assert "return 42" in target_file.read_text(encoding="utf-8")

    # Final summary checks
    assert "Execution Summary" in result.output
    assert "Stop Reason" in result.output
    assert "completed" in result.output
    assert "Changed Files" in result.output
    assert "src/feature.py" in result.output
    assert "Tests Run" in result.output
    assert "Failures" in result.output
    assert "Transcript Location" in result.output


def test_cli_approval_command_prompt_shows_command_arguments(tmp_path: Path) -> None:
    """Verify approval prompt for commands shows executable, arguments array, and working dir."""
    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="run_command",
                        arguments={"command": ["pytest", "-q"]},
                    )
                ],
                finish_reason="tool_calls",
            ),
            CompletionResponse(
                content="Tests completed.",
                finish_reason="stop",
            ),
        ]
    )

    with patch("termicode.cli.OpenAIProvider.from_env", return_value=fake_provider):
        result = runner.invoke(
            app,
            [
                "--workspace",
                str(tmp_path),
                "run",
                "run tests",
                "--workflow",
                "tests",
            ],
            input="y\n",
        )

    assert result.exit_code == 0
    assert "Action Approval Required: run_command" in result.output
    assert "Risk Level:" in result.output
    assert "EXECUTE" in result.output
    assert "Command:" in result.output
    assert "pytest -q" in result.output
    assert "Arguments:" in result.output
    assert "['pytest', '-q']" in result.output
    assert "Working Dir:" in result.output
    assert "workspace root" in result.output
    assert "Action approved." in result.output


def test_cli_approval_deny(tmp_path: Path) -> None:
    """Verify entering 'n' cancels the action and prevents disk modification."""
    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="write_file",
                        arguments={"path": "src/denied.py", "content": "print('bad')\n"},
                    )
                ],
                finish_reason="tool_calls",
            )
        ]
    )

    with patch("termicode.cli.OpenAIProvider.from_env", return_value=fake_provider):
        result = runner.invoke(
            app,
            [
                "--workspace",
                str(tmp_path),
                "run",
                "create denied.py",
                "--workflow",
                "generate",
            ],
            input="n\n",
        )

    assert result.exit_code == 0
    assert "Action denied by user." in result.output
    assert not (tmp_path / "src" / "denied.py").exists()
    assert "user_cancelled" in result.output
    assert "(None)" in result.output  # Changed files is None


def test_cli_approval_invalid_input_defaults_to_deny(tmp_path: Path) -> None:
    """Verify invalid input (e.g. 'maybe') defaults to deny without executing."""
    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="write_file",
                        arguments={"path": "src/invalid.py", "content": "code\n"},
                    )
                ],
                finish_reason="tool_calls",
            )
        ]
    )

    with patch("termicode.cli.OpenAIProvider.from_env", return_value=fake_provider):
        result = runner.invoke(
            app,
            [
                "--workspace",
                str(tmp_path),
                "run",
                "create file",
                "--workflow",
                "generate",
            ],
            input="maybe\n",
        )

    assert result.exit_code == 0
    assert "Invalid response 'maybe'. Defaulting to deny." in result.output
    assert not (tmp_path / "src" / "invalid.py").exists()
    assert "user_cancelled" in result.output


def test_cli_approval_noninteractive_session_defaults_to_deny(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify non-interactive session defaults to deny without waiting for user input."""
    monkeypatch.setattr("termicode.cli.is_non_interactive_session", lambda: True)

    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="write_file",
                        arguments={"path": "src/headless.py", "content": "headless\n"},
                    )
                ],
                finish_reason="tool_calls",
            )
        ]
    )

    with patch("termicode.cli.OpenAIProvider.from_env", return_value=fake_provider):
        result = runner.invoke(
            app,
            [
                "--workspace",
                str(tmp_path),
                "run",
                "create headless",
                "--workflow",
                "generate",
            ],
        )

    assert result.exit_code == 0
    assert "Non-interactive session detected" in result.output
    assert "action automatically denied" in result.output
    assert not (tmp_path / "src" / "headless.py").exists()
    assert "user_cancelled" in result.output


def test_cli_dry_run_option_shows_proposal_without_executing(tmp_path: Path) -> None:
    """Verify `--dry-run` displays proposal details and diff without executing changes."""
    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="write_file",
                        arguments={"path": "src/dry.py", "content": "print('dry')\n"},
                    )
                ],
                finish_reason="tool_calls",
            )
        ]
    )

    with patch("termicode.cli.OpenAIProvider.from_env", return_value=fake_provider):
        result = runner.invoke(
            app,
            [
                "--workspace",
                str(tmp_path),
                "run",
                "create dry.py",
                "--workflow",
                "generate",
                "--dry-run",
            ],
        )

    assert result.exit_code == 0
    assert "Dry-Run Proposal: write_file" in result.output
    assert "Dry-run active: proposed action displayed above without executing." in result.output
    assert not (tmp_path / "src" / "dry.py").exists()
    assert "(None)" in result.output


def test_cli_plan_option_generates_readonly_plan(tmp_path: Path) -> None:
    """Verify `--plan` and `termicode plan` run in read-only planning mode."""
    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                content=(
                    "1. Objective: Refactor database logic\n"
                    "2. Target files: src/db.py\n"
                    "3. Testing: Run pytest\n"
                ),
                finish_reason="stop",
            ),
            CompletionResponse(
                content=(
                    "1. Objective: Migrate user tables\n"
                    "2. Target files: src/models.py\n"
                    "3. Testing: Run pytest\n"
                ),
                finish_reason="stop",
            ),
        ]
    )

    with patch("termicode.cli.OpenAIProvider.from_env", return_value=fake_provider):
        # 1. Via `termicode run --plan`
        res_flag = runner.invoke(
            app,
            [
                "--workspace",
                str(tmp_path),
                "run",
                "refactor db",
                "--plan",
            ],
        )
        assert res_flag.exit_code == 0
        assert "understand" in res_flag.output
        assert "Refactor database logic" in res_flag.output
        assert "completed" in res_flag.output

        # 2. Via dedicated `termicode plan` command
        res_cmd = runner.invoke(
            app,
            [
                "--workspace",
                str(tmp_path),
                "plan",
                "migrate user tables",
            ],
        )
        assert res_cmd.exit_code == 0
        assert "understand" in res_cmd.output
        assert "Migrate user tables" in res_cmd.output
        assert "completed" in res_cmd.output


def test_cli_cancellation_ctrl_c(tmp_path: Path) -> None:
    """Verify Ctrl+C (KeyboardInterrupt) in approval prompt cleanly aborts without traceback."""
    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="write_file",
                        arguments={"path": "src/sigint.py", "content": "x = 1\n"},
                    )
                ],
                finish_reason="tool_calls",
            )
        ]
    )

    def raise_interrupt(*_args: object, **_kwargs: object) -> str:
        raise KeyboardInterrupt()

    with (
        patch("termicode.cli.OpenAIProvider.from_env", return_value=fake_provider),
        patch("builtins.input", side_effect=raise_interrupt),
    ):
        result = runner.invoke(
            app,
            [
                "--workspace",
                str(tmp_path),
                "run",
                "create sigint.py",
                "--workflow",
                "generate",
            ],
        )

    assert result.exit_code == 0
    assert "Operation cancelled by user (Ctrl+C)." in result.output
    assert not (tmp_path / "src" / "sigint.py").exists()
    assert "user_cancelled" in result.output
    assert "Traceback" not in result.output


def test_cli_no_unrestricted_force_switch() -> None:
    """Verify that an unrestricted `--force` switch is NOT present in the CLI."""
    help_res = runner.invoke(app, ["run", "--help"])
    assert help_res.exit_code == 0
    assert "--force" not in help_res.output

    # Invoking with --force is rejected as unrecognized option
    res = runner.invoke(app, ["run", "do something", "--force"])
    assert res.exit_code == 2
    assert "No such option: --force" in res.output


def test_cli_status_ollama_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that `termicode status` displays local Ollama configuration details."""
    monkeypatch.setenv("TERMICODE_PROVIDER", "ollama")
    monkeypatch.setenv("TERMICODE_MODEL", "qwen2.5-coder:7b")

    with patch(
        "termicode.cli.OpenAIProvider.check_local_endpoint",
        return_value=(True, "Connected"),
    ):
        result = runner.invoke(app, ["--workspace", str(tmp_path), "status"])

    assert result.exit_code == 0
    assert "ollama (Local Offline)" in result.output
    assert "qwen2.5-coder:7b" in result.output
    assert "http://localhost:11434/v1" in result.output
    assert "Connected" in result.output


def test_cli_run_ollama_unreachable_error_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify that attempting to execute a workflow with Ollama offline outputs clear advice."""
    monkeypatch.setenv("TERMICODE_PROVIDER", "ollama")

    with patch(
        "termicode.cli.OpenAIProvider.check_local_endpoint",
        return_value=(False, "Connection refused"),
    ):
        result = runner.invoke(
            app,
            [
                "--workspace",
                str(tmp_path),
                "understand",
                "explain architecture",
            ],
        )

    assert result.exit_code == 1
    assert "Local LLM Connection Error" in result.output
    assert "Local Ollama server is not reachable" in result.output
    assert "ollama serve" in result.output
    assert "ollama pull" in result.output


def test_cli_run_offline_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that --offline flag executes cleanly without external provider credentials."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TERMICODE_API_KEY", raising=False)

    result = runner.invoke(
        app,
        [
            "--workspace",
            str(tmp_path),
            "run",
            "explain architecture",
            "--workflow",
            "understand",
            "--offline",
        ],
    )
    assert result.exit_code == 0
    assert "Execution Summary" in result.output
    assert "completed" in result.output
    assert "offline engine" in result.output


def test_cli_run_provider_fake_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that -p fake executes cleanly without external provider credentials."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TERMICODE_API_KEY", raising=False)

    result = runner.invoke(
        app,
        [
            "--workspace",
            str(tmp_path),
            "run",
            "explain codebase",
            "--workflow",
            "understand",
            "-p",
            "fake",
        ],
    )
    assert result.exit_code == 0
    assert "Execution Summary" in result.output
    assert "completed" in result.output


def test_cli_shortcut_commands_with_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify dedicated shortcut commands work directly with --offline."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TERMICODE_API_KEY", raising=False)

    for shortcut in ["understand", "review"]:
        result = runner.invoke(
            app,
            ["--workspace", str(tmp_path), shortcut, "analyze code", "--offline"],
        )
        assert result.exit_code == 0
        assert "Execution Summary" in result.output
