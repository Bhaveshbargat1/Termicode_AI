"""Tests for version control, automated Git checkpoints, and rollback integration."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from termicode.agent.models import AgentRequest, StopReason
from termicode.agent.runtime import AgentRuntime
from termicode.agent.workflows import WorkflowType
from termicode.cli import app
from termicode.providers.base import CompletionResponse, ToolCall
from termicode.providers.fake import FakeProvider
from termicode.tools.command import RunCommandTool
from termicode.tools.filesystem import ReadFileTool, WriteFileTool
from termicode.tools.registry import ToolRegistry
from termicode.version_control import (
    CheckpointManager,
    CheckpointNotFoundError,
    GitRepositoryError,
    init_git_repo,
    is_git_repo,
)
from termicode.workspace import WorkspaceBoundary

runner = CliRunner()


def setup_git_workspace(tmp_path: Path) -> WorkspaceBoundary:
    """Helper to initialize a Git workspace with an initial commit."""
    boundary = WorkspaceBoundary(tmp_path)
    boundary.ensure_termicode_dir()
    init_git_repo(boundary.root)
    return boundary


# ==============================================================================
# 1. Version Control Init & Repository Verification Tests
# ==============================================================================


def test_init_git_repo_creates_git_and_initial_commit(tmp_path: Path) -> None:
    """Verify init_git_repo creates a repository, initial commit, and .gitignore."""
    assert not is_git_repo(tmp_path)
    created, msg = init_git_repo(tmp_path)
    assert created is True
    assert is_git_repo(tmp_path)

    gitignore = tmp_path / ".gitignore"
    assert gitignore.is_file()
    assert ".termicode/" in gitignore.read_text(encoding="utf-8")

    # Verify HEAD commit exists
    res = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    assert len(res.stdout.strip()) == 40


def test_checkpoint_manager_requires_git_repo(tmp_path: Path) -> None:
    """Verify CheckpointManager raises GitRepositoryError when run outside Git."""
    mgr = CheckpointManager(tmp_path)
    assert mgr.is_repo() is False

    with pytest.raises(GitRepositoryError, match="Run 'termicode init'"):
        mgr.ensure_git_repo()

    with pytest.raises(GitRepositoryError, match="Run 'termicode init'"):
        mgr.create_checkpoint("test")

    with pytest.raises(GitRepositoryError, match="Run 'termicode init'"):
        mgr.rollback()


# ==============================================================================
# 2. Checkpoint Creation & Rollback State Tests
# ==============================================================================


def test_checkpoint_manual_save_and_rollback_restores_state(tmp_path: Path) -> None:
    """Scenario: create checkpoint, modify file and create untracked file, rollback."""
    boundary = setup_git_workspace(tmp_path)
    mgr = CheckpointManager(boundary.root)

    # Base committed file
    file1 = boundary.root / "file1.txt"
    file1.write_text("version 1", encoding="utf-8")
    mgr.commit_changes("Add file1.txt")

    # State S1: User creates modifications and an untracked file
    file1.write_text("version 2 (good modification)", encoding="utf-8")
    untracked_doc = boundary.root / "notes.txt"
    untracked_doc.write_text("important user notes", encoding="utf-8")

    assert mgr.has_uncommitted_changes() is True
    uncommitted = mgr.get_uncommitted_changes()
    assert any("file1.txt" in f for f in uncommitted)
    assert any("notes.txt" in f for f in uncommitted)

    # Save checkpoint at S1
    cp = mgr.create_checkpoint(message="v2 good state", checkpoint_type="manual")
    assert cp.has_stash is True
    assert cp.stash_oid is not None

    # State S2: Something causes corruption
    file1.write_text("CORRUPTED VERSION 3", encoding="utf-8")
    broken_file = boundary.root / "broken.py"
    broken_file.write_text("def broken(): raise RuntimeError()", encoding="utf-8")

    # Trigger rollback to checkpoint S1
    restored_cp = mgr.rollback(cp.id)
    assert restored_cp.id == cp.id

    # Verify workspace is restored to S1 exactly
    assert file1.read_text(encoding="utf-8") == "version 2 (good modification)"
    assert untracked_doc.is_file()
    assert untracked_doc.read_text(encoding="utf-8") == "important user notes"
    assert not broken_file.exists()


def test_checkpoint_clean_state_rollback_to_head(tmp_path: Path) -> None:
    """Scenario: checkpoint on clean HEAD, modify file, rollback restores clean state."""
    boundary = setup_git_workspace(tmp_path)
    mgr = CheckpointManager(boundary.root)

    source_file = boundary.root / "app.py"
    source_file.write_text("print('stable')", encoding="utf-8")
    mgr.commit_changes("Add stable app.py")

    # Clean working tree checkpoint
    cp = mgr.create_checkpoint(message="stable checkpoint", checkpoint_type="manual")
    assert cp.has_stash is False

    # Introduce changes
    source_file.write_text("print('unstable')", encoding="utf-8")
    garbage = boundary.root / "garbage.tmp"
    garbage.write_text("temp", encoding="utf-8")

    # Rollback restores clean HEAD
    mgr.rollback(cp.id)
    assert source_file.read_text(encoding="utf-8") == "print('stable')"
    assert not garbage.exists()


def test_rollback_nonexistent_checkpoint_raises_error(tmp_path: Path) -> None:
    """Verify rollback raises CheckpointNotFoundError for invalid ID."""
    boundary = setup_git_workspace(tmp_path)
    mgr = CheckpointManager(boundary.root)

    with pytest.raises(CheckpointNotFoundError, match="Checkpoint 'invalid_id' not found"):
        mgr.rollback("invalid_id")


# ==============================================================================
# 3. Agent Execution Loop Auto-Checkpoint & Rollback Tests
# ==============================================================================


def test_agent_loop_auto_checkpoints_and_rolls_back_on_failure(tmp_path: Path) -> None:
    """Scenario: failing tool triggers automatic rollback restoring workspace."""
    boundary = setup_git_workspace(tmp_path)
    target_file = boundary.root / "config.json"
    target_file.write_text('{"status": "ok"}', encoding="utf-8")
    CheckpointManager(boundary.root).commit_changes("Initial config")

    registry = ToolRegistry()
    registry.register(WriteFileTool())
    registry.register(RunCommandTool())
    registry.register(ReadFileTool())

    # Step 1: Model modifies config.json
    # Step 2: Model runs a failing test command that triggers repair exhaustion
    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                content="Modifying config.json",
                tool_calls=[
                    ToolCall(
                        id="call_write",
                        name="write_file",
                        arguments={"path": "config.json", "content": '{"status": "corrupted"}'},
                    )
                ],
                finish_reason="tool_calls",
            ),
            CompletionResponse(
                content="Running tests that fail",
                tool_calls=[
                    ToolCall(
                        id="call_fail_1",
                        name="run_command",
                        arguments={"command": ["python", "-c", "import sys; sys.exit(1)"]},
                    )
                ],
                finish_reason="tool_calls",
            ),
            # Repeated failures to exhaust repairs
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="call_fail_2",
                        name="run_command",
                        arguments={"command": ["python", "-c", "import sys; sys.exit(1)"]},
                    )
                ],
                finish_reason="tool_calls",
            ),
        ]
    )

    runtime = AgentRuntime(
        provider=fake_provider,
        registry=registry,
        approval_handler=lambda a, d: True,
    )

    req = AgentRequest(
        task="Update config safely",
        workspace=boundary,
        workflow=WorkflowType.GENERATE,
        max_repairs=2,
        auto_rollback=True,
    )

    result = runtime.run(req)

    # Task should halt with max repairs reached
    assert result.stop_reason == StopReason.MAX_REPAIRS_REACHED
    assert result.rolled_back is True
    assert result.rollback_checkpoint_id is not None

    # Verify that config.json was reverted back to original state
    assert target_file.read_text(encoding="utf-8") == '{"status": "ok"}'


def test_agent_loop_rollback_confirmation_denial(tmp_path: Path) -> None:
    """Scenario: user denies rollback confirmation; changes are kept."""
    boundary = setup_git_workspace(tmp_path)
    target_file = boundary.root / "data.txt"
    target_file.write_text("original", encoding="utf-8")
    CheckpointManager(boundary.root).commit_changes("Initial data")

    registry = ToolRegistry()
    registry.register(WriteFileTool())
    registry.register(RunCommandTool())

    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="call_write",
                        name="write_file",
                        arguments={"path": "data.txt", "content": "modified_unreverted"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="call_fail",
                        name="run_command",
                        arguments={"command": ["python", "-c", "raise ValueError()"]},
                    )
                ],
                finish_reason="tool_calls",
            ),
        ]
    )

    # Rollback handler explicitly returns False (user rejects rollback)
    rollback_mock = MagicMock(return_value=False)

    runtime = AgentRuntime(
        provider=fake_provider,
        registry=registry,
        approval_handler=lambda a, d: True,
        rollback_handler=rollback_mock,
    )

    req = AgentRequest(
        task="Modify data",
        workspace=boundary,
        workflow=WorkflowType.GENERATE,
        max_repairs=1,
    )

    result = runtime.run(req)
    assert result.stop_reason == StopReason.MAX_REPAIRS_REACHED
    assert rollback_mock.called
    assert result.rolled_back is False

    # Changes remain un-reverted because user denied rollback
    assert target_file.read_text(encoding="utf-8") == "modified_unreverted"


def test_agent_auto_commit_on_successful_plan(tmp_path: Path) -> None:
    """Scenario: after successful plan execution, changes are optionally committed."""
    boundary = setup_git_workspace(tmp_path)
    registry = ToolRegistry()
    registry.register(WriteFileTool())

    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="call_write",
                        name="write_file",
                        arguments={"path": "feature.py", "content": "def feature(): return True"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            CompletionResponse(
                content="Feature implementation complete.",
                finish_reason="stop",
            ),
        ]
    )

    runtime = AgentRuntime(
        provider=fake_provider,
        registry=registry,
        approval_handler=lambda a, d: True,
    )

    req = AgentRequest(
        task="Implement feature.py",
        workspace=boundary,
        workflow=WorkflowType.GENERATE,
        auto_commit=True,
        commit_message="Add feature.py implementation",
    )

    result = runtime.run(req)
    assert result.stop_reason == StopReason.COMPLETED
    assert result.committed_hash is not None

    # Verify commit log in git
    log_res = subprocess.run(
        ["git", "log", "-1", "--pretty=%B"],
        cwd=boundary.root,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Add feature.py implementation" in log_res.stdout


# ==============================================================================
# 4. CLI Commands (init, checkpoint, rollback, checkpoints)
# ==============================================================================


def test_cli_checkpoint_requires_git_repo(tmp_path: Path) -> None:
    """Verify `termicode checkpoint` warns and exits 1 on non-git workspace."""
    result = runner.invoke(app, ["--workspace", str(tmp_path), "checkpoint"])
    assert result.exit_code == 1
    assert "Workspace is not a Git repository" in result.output
    assert "termicode init" in result.output


def test_cli_init_initializes_git_repo(tmp_path: Path) -> None:
    """Verify `termicode init` initializes Git repo and metadata."""
    assert not is_git_repo(tmp_path)
    result = runner.invoke(app, ["--workspace", str(tmp_path), "init"])
    assert result.exit_code == 0
    assert "Initialized Git repository" in result.output
    assert is_git_repo(tmp_path)


def test_cli_checkpoint_and_rollback_workflow(tmp_path: Path) -> None:
    """Verify full CLI flow: init -> checkpoint -> modify -> rollback -y."""
    # 1. Initialize
    res_init = runner.invoke(app, ["--workspace", str(tmp_path), "init"])
    assert res_init.exit_code == 0

    test_file = tmp_path / "hello.txt"
    test_file.write_text("Hello World", encoding="utf-8")

    # 2. Checkpoint
    res_cp = runner.invoke(
        app,
        ["--workspace", str(tmp_path), "checkpoint", "Initial hello state"],
    )
    assert res_cp.exit_code == 0
    assert "Created checkpoint:" in res_cp.output

    # 3. Checkpoints list
    res_list = runner.invoke(app, ["--workspace", str(tmp_path), "checkpoints"])
    assert res_list.exit_code == 0
    assert "Recorded Git Checkpoints" in res_list.output
    assert "Initial" in res_list.output and "hello" in res_list.output

    # 4. Corrupt file and add temp file
    test_file.write_text("Corrupted Text", encoding="utf-8")
    temp_junk = tmp_path / "junk.tmp"
    temp_junk.write_text("junk", encoding="utf-8")

    # 5. Rollback
    res_rb = runner.invoke(app, ["--workspace", str(tmp_path), "rollback", "-y"])
    assert res_rb.exit_code == 0
    assert "Workspace successfully rolled back to checkpoint:" in res_rb.output

    # 6. Verify restored state
    assert test_file.read_text(encoding="utf-8") == "Hello World"
    assert not temp_junk.exists()
