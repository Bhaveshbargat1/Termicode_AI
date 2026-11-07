"""Unit tests for PolicyEngine deterministic evaluation, previews, and workflow constraints."""

from pathlib import Path

from termicode.agent.models import ProposedAction
from termicode.agent.policy import PolicyEngine
from termicode.agent.workflows import WorkflowType, get_workflow_profile
from termicode.tools.command import RunCommandTool
from termicode.tools.filesystem import (
    ApplyPatchTool,
    ListFilesTool,
    ReadFileTool,
    SearchTextTool,
    WriteFileTool,
)
from termicode.tools.registry import ToolRegistry
from termicode.workspace import WorkspaceBoundary


def create_test_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ListFilesTool())
    registry.register(ReadFileTool())
    registry.register(SearchTextTool())
    registry.register(WriteFileTool())
    registry.register(ApplyPatchTool())
    registry.register(RunCommandTool())
    return registry


def test_policy_permits_final_action(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    engine = PolicyEngine(create_test_registry())
    profile = get_workflow_profile(WorkflowType.UNDERSTAND)

    action = ProposedAction(kind="final", rationale="Explaining task")
    decision = engine.evaluate(action, profile, boundary)

    assert decision.status == "permit"
    assert "permitted" in decision.reason.lower()


def test_policy_denies_unsupported_kind(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    engine = PolicyEngine(create_test_registry())
    profile = get_workflow_profile(WorkflowType.UNDERSTAND)

    action = ProposedAction(kind="arbitrary_kind")
    decision = engine.evaluate(action, profile, boundary)

    assert decision.status == "deny"
    assert "unsupported action kind" in decision.reason.lower()


def test_policy_flags_unknown_tool(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    engine = PolicyEngine(create_test_registry())
    profile = get_workflow_profile(WorkflowType.UNDERSTAND)

    action = ProposedAction(kind="tool_call", tool_name="unregistered_tool")
    decision = engine.evaluate(action, profile, boundary)

    assert decision.status == "unknown_tool"
    assert "not registered" in decision.reason.lower()


def test_policy_denies_mutating_tools_in_readonly_workflows(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    engine = PolicyEngine(create_test_registry())

    # In UNDERSTAND or REVIEW workflow, write_file and run_command must be denied
    for wf in [WorkflowType.UNDERSTAND, WorkflowType.REVIEW]:
        profile = get_workflow_profile(wf)

        # Write tool denied
        write_action = ProposedAction(
            kind="tool_call",
            tool_name="write_file",
            arguments={"path": "test.txt", "content": "hi"},
        )
        dec_write = engine.evaluate(write_action, profile, boundary)
        assert dec_write.status == "deny"
        assert "prohibited" in dec_write.reason.lower()

        # Command tool denied
        cmd_action = ProposedAction(
            kind="tool_call",
            tool_name="run_command",
            arguments={"command": ["pytest"]},
        )
        dec_cmd = engine.evaluate(cmd_action, profile, boundary)
        assert dec_cmd.status == "deny"
        assert "prohibited" in dec_cmd.reason.lower()


def test_policy_flags_invalid_arguments_schema(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    engine = PolicyEngine(create_test_registry())
    profile = get_workflow_profile(WorkflowType.UNDERSTAND)

    # Missing required 'path'
    action = ProposedAction(kind="tool_call", tool_name="read_file", arguments={})
    decision = engine.evaluate(action, profile, boundary)

    assert decision.status == "invalid_arguments"
    assert "failed schema validation" in decision.reason.lower()


def test_policy_requires_approval_with_diff_preview_for_write(tmp_path: Path) -> None:
    existing_file = tmp_path / "hello.py"
    existing_file.write_text("print('old')\n", encoding="utf-8")

    boundary = WorkspaceBoundary(tmp_path)
    engine = PolicyEngine(create_test_registry())
    profile = get_workflow_profile(WorkflowType.GENERATE)

    action = ProposedAction(
        kind="tool_call",
        tool_name="write_file",
        arguments={"path": "hello.py", "content": "print('new')\n"},
    )
    decision = engine.evaluate(action, profile, boundary)

    assert decision.status == "needs_approval"
    assert decision.preview is not None
    assert "-print('old')" in decision.preview
    assert "+print('new')" in decision.preview


def test_policy_requires_approval_with_preview_for_command(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    engine = PolicyEngine(create_test_registry())
    profile = get_workflow_profile(WorkflowType.GENERATE)

    action = ProposedAction(
        kind="tool_call",
        tool_name="run_command",
        arguments={"command": ["pytest", "-q"]},
    )
    decision = engine.evaluate(action, profile, boundary)

    assert decision.status == "needs_approval"
    assert decision.preview is not None
    assert "pytest -q" in decision.preview


def test_policy_permits_read_tools_in_mutating_workflows(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    engine = PolicyEngine(create_test_registry())
    profile = get_workflow_profile(WorkflowType.GENERATE)

    action = ProposedAction(
        kind="tool_call",
        tool_name="read_file",
        arguments={"path": "README.md"},
    )
    decision = engine.evaluate(action, profile, boundary)

    assert decision.status == "permit"
    assert "permitted" in decision.reason.lower()
