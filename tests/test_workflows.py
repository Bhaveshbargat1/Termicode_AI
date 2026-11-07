"""Tests for workflow profiles, authorization rules, and read-only safety boundaries."""

from __future__ import annotations

from pathlib import Path

import pytest

from termicode.agent.models import AgentRequest, ProposedAction, StopReason
from termicode.agent.policy import PolicyEngine
from termicode.agent.runtime import AgentRuntime
from termicode.agent.workflows import (
    WorkflowProfile,
    WorkflowType,
    get_workflow_profile,
    list_workflows,
)
from termicode.providers.base import CompletionResponse, ToolCall
from termicode.providers.fake import FakeProvider
from termicode.tools.base import RiskLevel
from termicode.tools.registry import create_default_registry
from termicode.workspace import WorkspaceBoundary

ALL_PROFILES = [
    WorkflowType.GENERATE,
    WorkflowType.DEBUG,
    WorkflowType.UNDERSTAND,
    WorkflowType.TESTS,
    WorkflowType.REVIEW,
    WorkflowType.REFACTOR,
]


def test_all_six_profiles_exist() -> None:
    """Verify that all six required workflow profiles are registered and listed."""
    registered = list_workflows()
    expected = ["debug", "generate", "refactor", "review", "tests", "understand"]
    assert registered == expected
    for name in expected:
        profile = get_workflow_profile(name)
        assert isinstance(profile, WorkflowProfile)
        assert profile.name == name


def test_profile_field_completeness() -> None:
    """Verify that each workflow profile defines all required fields with valid values."""
    for wf in ALL_PROFILES:
        profile = get_workflow_profile(wf)
        assert len(profile.name) > 0
        assert len(profile.description) > 0
        assert len(profile.allowed_tools) > 0
        assert len(profile.allowed_risk_levels) > 0
        assert len(profile.system_instructions) > 0
        assert len(profile.success_criteria) > 0
        assert profile.maximum_step_budget >= 1
        assert profile.max_step_budget == profile.maximum_step_budget
        assert profile.system_instructions in profile.system_prompt
        assert profile.success_criteria in profile.system_prompt


def test_understand_profile_strict_readonly_isolation() -> None:
    """Prove understand profile has zero write or execute access."""
    profile = get_workflow_profile(WorkflowType.UNDERSTAND)

    # Risk level policy
    assert profile.allowed_risk_levels == {RiskLevel.READ}
    assert RiskLevel.WRITE not in profile.allowed_risk_levels
    assert RiskLevel.EXECUTE not in profile.allowed_risk_levels
    assert profile.is_read_only is True
    assert profile.allows_read is True
    assert profile.allows_write is False
    assert profile.allows_execute is False

    # Allowed tools
    assert profile.allowed_tools == {"list_files", "read_file", "search_text"}
    assert "write_file" not in profile.allowed_tools
    assert "apply_patch" not in profile.allowed_tools
    assert "run_command" not in profile.allowed_tools


def test_review_profile_strict_readonly_isolation() -> None:
    """Prove review profile has zero write or execute access."""
    profile = get_workflow_profile(WorkflowType.REVIEW)

    # Risk level policy
    assert profile.allowed_risk_levels == {RiskLevel.READ}
    assert RiskLevel.WRITE not in profile.allowed_risk_levels
    assert RiskLevel.EXECUTE not in profile.allowed_risk_levels
    assert profile.is_read_only is True
    assert profile.allows_read is True
    assert profile.allows_write is False
    assert profile.allows_execute is False

    # Allowed tools
    assert profile.allowed_tools == {"list_files", "read_file", "search_text"}
    assert "write_file" not in profile.allowed_tools
    assert "apply_patch" not in profile.allowed_tools
    assert "run_command" not in profile.allowed_tools


def test_mutating_profiles_have_write_and_execute_permissions() -> None:
    """Verify that generate, debug, tests, and refactor workflows have mutating capabilities."""
    mutating = [
        WorkflowType.GENERATE,
        WorkflowType.DEBUG,
        WorkflowType.TESTS,
        WorkflowType.REFACTOR,
    ]
    for wf in mutating:
        profile = get_workflow_profile(wf)
        assert RiskLevel.WRITE in profile.allowed_risk_levels
        assert RiskLevel.EXECUTE in profile.allowed_risk_levels
        assert profile.allows_write is True
        assert profile.allows_execute is True
        assert profile.is_read_only is False
        assert {"write_file", "apply_patch", "run_command"}.issubset(profile.allowed_tools)


def test_policy_engine_strictly_denies_write_and_execute_in_understand(tmp_path: Path) -> None:
    """Prove PolicyEngine rejects write and execute tool proposals in understand workflow."""
    boundary = WorkspaceBoundary(tmp_path)
    engine = PolicyEngine(create_default_registry())
    profile = get_workflow_profile(WorkflowType.UNDERSTAND)

    # 1. write_file attempt
    write_act = ProposedAction(
        kind="tool_call",
        tool_name="write_file",
        arguments={"path": "out.txt", "content": "data"},
    )
    dec_write = engine.evaluate(write_act, profile, boundary)
    assert dec_write.status == "deny"
    assert "prohibited" in dec_write.reason.lower() or "not in the allowlist" in dec_write.reason

    # 2. apply_patch attempt
    patch_act = ProposedAction(
        kind="tool_call",
        tool_name="apply_patch",
        arguments={"path": "out.txt", "patch": "diff"},
    )
    dec_patch = engine.evaluate(patch_act, profile, boundary)
    assert dec_patch.status == "deny"

    # 3. run_command attempt
    cmd_act = ProposedAction(
        kind="tool_call",
        tool_name="run_command",
        arguments={"command": ["pytest"]},
    )
    dec_cmd = engine.evaluate(cmd_act, profile, boundary)
    assert dec_cmd.status == "deny"

    # 4. read tools permitted
    read_act = ProposedAction(
        kind="tool_call",
        tool_name="read_file",
        arguments={"path": "test.txt"},
    )
    dec_read = engine.evaluate(read_act, profile, boundary)
    assert dec_read.status == "permit"


def test_policy_engine_strictly_denies_write_and_execute_in_review(tmp_path: Path) -> None:
    """Prove PolicyEngine rejects write and execute tool proposals in review workflow."""
    boundary = WorkspaceBoundary(tmp_path)
    engine = PolicyEngine(create_default_registry())
    profile = get_workflow_profile(WorkflowType.REVIEW)

    # 1. write_file attempt
    write_act = ProposedAction(
        kind="tool_call",
        tool_name="write_file",
        arguments={"path": "notes.md", "content": "review notes"},
    )
    dec_write = engine.evaluate(write_act, profile, boundary)
    assert dec_write.status == "deny"

    # 2. apply_patch attempt
    patch_act = ProposedAction(
        kind="tool_call",
        tool_name="apply_patch",
        arguments={"path": "notes.md", "patch": "diff"},
    )
    dec_patch = engine.evaluate(patch_act, profile, boundary)
    assert dec_patch.status == "deny"

    # 3. run_command attempt
    cmd_act = ProposedAction(
        kind="tool_call",
        tool_name="run_command",
        arguments={"command": ["git", "status"]},
    )
    dec_cmd = engine.evaluate(cmd_act, profile, boundary)
    assert dec_cmd.status == "deny"

    # 4. read tools permitted
    search_act = ProposedAction(
        kind="tool_call",
        tool_name="search_text",
        arguments={"query": "def test"},
    )
    dec_search = engine.evaluate(search_act, profile, boundary)
    assert dec_search.status == "permit"


def test_agent_runtime_tool_declarations_exclude_write_and_execute_in_readonly_workflows(
    tmp_path: Path,
) -> None:
    """Prove AgentRuntime never exposes write/execute tools to provider in understand and review."""
    boundary = WorkspaceBoundary(tmp_path)

    for wf in [WorkflowType.UNDERSTAND, WorkflowType.REVIEW]:
        fake_provider = FakeProvider(
            responses=[CompletionResponse(content="Done.", finish_reason="stop")]
        )
        runtime = AgentRuntime(provider=fake_provider, registry=create_default_registry())
        req = AgentRequest(task="Inspect repository", workspace=boundary, workflow=wf)
        runtime.run(req)

        # Inspect tool declarations passed to provider
        assert len(fake_provider.calls) == 1
        declared_names = {t.name for t in fake_provider.calls[0].tools}

        assert declared_names == {"list_files", "read_file", "search_text"}
        assert "write_file" not in declared_names
        assert "apply_patch" not in declared_names
        assert "run_command" not in declared_names


def test_agent_runtime_aborts_on_attempted_write_in_understand_workflow(tmp_path: Path) -> None:
    """Prove AgentRuntime terminates with POLICY_REJECTED if model proposes write in understand."""
    boundary = WorkspaceBoundary(tmp_path)
    target_file = tmp_path / "injected.py"

    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="write_file",
                        arguments={"path": "injected.py", "content": "malicious = True\n"},
                    )
                ],
                finish_reason="tool_calls",
            )
        ]
    )

    runtime = AgentRuntime(
        provider=fake_provider,
        registry=create_default_registry(),
        approval_handler=lambda _act, _dec: True,  # Even if approved, policy must deny upfront
    )
    req = AgentRequest(
        task="Try writing a file",
        workspace=boundary,
        workflow=WorkflowType.UNDERSTAND,
    )

    result = runtime.run(req)

    assert result.stop_reason == StopReason.POLICY_REJECTED
    assert not target_file.exists()
    assert result.changed_files == []


def test_agent_runtime_aborts_on_attempted_execute_in_understand_workflow(tmp_path: Path) -> None:
    """Prove AgentRuntime terminates with POLICY_REJECTED on command in understand."""
    boundary = WorkspaceBoundary(tmp_path)

    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="run_command",
                        arguments={"command": ["pytest"]},
                    )
                ],
                finish_reason="tool_calls",
            )
        ]
    )

    runtime = AgentRuntime(
        provider=fake_provider,
        registry=create_default_registry(),
        approval_handler=lambda _act, _dec: True,
    )
    req = AgentRequest(
        task="Try executing tests",
        workspace=boundary,
        workflow=WorkflowType.UNDERSTAND,
    )

    result = runtime.run(req)

    assert result.stop_reason == StopReason.POLICY_REJECTED
    assert result.commands_run == []


def test_agent_runtime_aborts_on_attempted_write_in_review_workflow(tmp_path: Path) -> None:
    """Prove AgentRuntime terminates with POLICY_REJECTED if model proposes write in review."""
    boundary = WorkspaceBoundary(tmp_path)
    target_file = tmp_path / "review_notes.txt"

    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="write_file",
                        arguments={"path": "review_notes.txt", "content": "findings"},
                    )
                ],
                finish_reason="tool_calls",
            )
        ]
    )

    runtime = AgentRuntime(
        provider=fake_provider,
        registry=create_default_registry(),
        approval_handler=lambda _act, _dec: True,
    )
    req = AgentRequest(
        task="Review code and save notes",
        workspace=boundary,
        workflow=WorkflowType.REVIEW,
    )

    result = runtime.run(req)

    assert result.stop_reason == StopReason.POLICY_REJECTED
    assert not target_file.exists()
    assert result.changed_files == []


def test_agent_runtime_aborts_on_attempted_execute_in_review_workflow(tmp_path: Path) -> None:
    """Prove AgentRuntime terminates with POLICY_REJECTED if model proposes command in review."""
    boundary = WorkspaceBoundary(tmp_path)

    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="run_command",
                        arguments={"command": ["git", "diff"]},
                    )
                ],
                finish_reason="tool_calls",
            )
        ]
    )

    runtime = AgentRuntime(
        provider=fake_provider,
        registry=create_default_registry(),
        approval_handler=lambda _act, _dec: True,
    )
    req = AgentRequest(
        task="Run git diff",
        workspace=boundary,
        workflow=WorkflowType.REVIEW,
    )

    result = runtime.run(req)

    assert result.stop_reason == StopReason.POLICY_REJECTED
    assert result.commands_run == []


def test_workflow_step_budgets_respected(tmp_path: Path) -> None:
    """Verify that the single runtime respects the workflow profile's default step budget."""
    boundary = WorkspaceBoundary(tmp_path)
    readme = tmp_path / "README.md"
    readme.write_text("Hello world", encoding="utf-8")

    # UNDERSTAND profile has maximum_step_budget = 8
    understand_profile = get_workflow_profile(WorkflowType.UNDERSTAND)
    assert understand_profile.maximum_step_budget == 8

    # Infinite read proposals
    infinite_reads = [
        CompletionResponse(
            tool_calls=[ToolCall(id=f"c{i}", name="read_file", arguments={"path": "README.md"})],
            finish_reason="tool_calls",
        )
        for i in range(15)
    ]

    fake_provider = FakeProvider(responses=infinite_reads)
    runtime = AgentRuntime(provider=fake_provider, registry=create_default_registry())

    # No max_steps specified in request -> must use profile budget (8)
    req = AgentRequest(
        task="Read files indefinitely",
        workspace=boundary,
        workflow=WorkflowType.UNDERSTAND,
    )

    result = runtime.run(req)

    assert result.stop_reason == StopReason.MAX_STEPS_REACHED
    assert result.steps_used == 8


def test_get_workflow_profile_case_insensitive() -> None:
    """Verify workflow profile lookup is case-insensitive."""
    assert get_workflow_profile("UNDERSTAND").name == "understand"
    assert get_workflow_profile("Review").name == "review"
    assert get_workflow_profile("GeNeRaTe").name == "generate"
    assert get_workflow_profile(WorkflowType.DEBUG).name == "debug"


def test_get_workflow_profile_unknown_raises() -> None:
    """Verify unknown workflow profile names raise ValueError with helpful options."""
    with pytest.raises(ValueError, match="Unknown workflow profile 'unknown_wf'"):
        get_workflow_profile("unknown_wf")
