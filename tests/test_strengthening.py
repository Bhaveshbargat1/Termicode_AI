"""Deterministic test suite strengthening TermiCode AI safety, policy, and runtime boundaries.

Covers:
1. Workspace escape prevention (path traversal, symlinks, search, patches, sensitive paths)
2. Policy decisions (permit, deny, needs_approval, invalid_arguments, unknown_tool)
3. Write and shell approval requirements (interactive approve/deny, cancellation, dry-run)
4. Subprocess timeout and output limits (timeouts, stdout/stderr capping, non-zero exits)
5. Malformed model tool calls (missing fields, invalid types, repair counters, resets)
6. Agent loop bounds (max_steps limits, profile budgets, direct completion)
7. Provider error handling (unrecoverable auth errors, recoverable retries, secret scrubbing)
8. Secret redaction (tokens, API keys, URLs, headers, persistent transcript/summary scrubbing)
9. Workflow tool restrictions (read-only tool exclusion, mutating profile permissions)
10. Final transcript and summary correctness (audit fields, valid JSONL events, unresolved issues)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from termicode.agent.memory import LocalMemory
from termicode.agent.models import (
    AgentRequest,
    ProposedAction,
    StopReason,
)
from termicode.agent.policy import PolicyEngine
from termicode.agent.runtime import AgentRuntime
from termicode.agent.workflows import (
    WorkflowType,
    get_workflow_profile,
)
from termicode.cli import make_cli_approval_handler
from termicode.providers.base import (
    CompletionResponse,
    ProviderAuthError,
    ProviderRateLimitError,
    ToolCall,
)
from termicode.providers.fake import FakeProvider
from termicode.providers.redaction import redact_headers, redact_secrets
from termicode.runner import (
    CommandRunner,
    UnsafeArgumentError,
    validate_command,
)
from termicode.tools.base import RiskLevel, ToolContext
from termicode.tools.command import RunCommandTool
from termicode.tools.filesystem import (
    ApplyPatchTool,
    ListFilesTool,
    ReadFileTool,
    SearchTextTool,
    WriteFileTool,
)
from termicode.tools.registry import ToolRegistry
from termicode.workspace import (
    SensitivePathError,
    WorkspaceBoundary,
    WorkspaceEscapeError,
)


def create_full_registry() -> ToolRegistry:
    """Helper to instantiate registry with all standard tools."""
    registry = ToolRegistry()
    registry.register(ListFilesTool())
    registry.register(ReadFileTool())
    registry.register(SearchTextTool())
    registry.register(WriteFileTool())
    registry.register(ApplyPatchTool())
    registry.register(RunCommandTool())
    return registry


# ===========================================================================
# 1. Workspace Escape Prevention
# ===========================================================================


class TestWorkspaceEscapePrevention:
    """Verify deterministic boundary enforcement against traversal and sensitive files."""

    def test_resolve_safe_rejects_deep_nested_traversal(self, tmp_path: Path) -> None:
        boundary = WorkspaceBoundary(tmp_path)
        nested_dir = tmp_path / "a" / "b" / "c"
        nested_dir.mkdir(parents=True)

        # Deep escape escaping out of tmp_path
        with pytest.raises(WorkspaceEscapeError, match="Path traversal detected"):
            boundary.resolve_safe("a/b/c/../../../../escaped_target.txt")

    def test_search_text_rejects_workspace_escape_directory(self, tmp_path: Path) -> None:
        boundary = WorkspaceBoundary(tmp_path)
        tool = SearchTextTool()
        ctx = ToolContext(workspace=boundary)

        result = tool.run({"query": "def hello", "directory": "../../"}, ctx)
        assert result.ok is False
        assert result.error is not None
        assert "Path traversal detected" in result.error or "outside" in result.error

    def test_apply_patch_rejects_escaping_target_file(self, tmp_path: Path) -> None:
        boundary = WorkspaceBoundary(tmp_path)
        tool = ApplyPatchTool()
        ctx = ToolContext(workspace=boundary, user_confirmed=True)

        patch_content = (
            "--- a/../../escaped.py\n+++ b/../../escaped.py\n@@ -1,1 +1,1 @@\n-old\n+new\n"
        )
        result = tool.run({"path": "../../escaped.py", "patch": patch_content}, ctx)
        assert result.ok is False
        assert result.error is not None
        assert "Path traversal detected" in result.error or "outside" in result.error

    def test_command_runner_arguments_reject_path_traversal(self, tmp_path: Path) -> None:
        boundary = WorkspaceBoundary(tmp_path)

        with pytest.raises(UnsafeArgumentError, match="escapes the workspace boundary"):
            validate_command(
                ["python", "-m", "pytest", "../../outside_tests"],
                workspace=boundary,
            )

    @pytest.mark.parametrize(
        "sensitive_rel_path",
        [
            "subfolder/.env",
            "nested/dir/.env.production",
            "nested/.git/config",
            "keys/.ssh/id_rsa",
            "creds/credentials.json",
            "certs/mycert.pem",
            "certs/server.key",
            ".ENV",
            "ID_RSA",
            "SECRET.KEY",
        ],
    )
    def test_sensitive_paths_blocked_recursively_and_case_insensitively(
        self, tmp_path: Path, sensitive_rel_path: str
    ) -> None:
        boundary = WorkspaceBoundary(tmp_path)
        assert boundary.is_sensitive(tmp_path / sensitive_rel_path) is True

        with pytest.raises(SensitivePathError, match="Access denied"):
            boundary.resolve_safe(sensitive_rel_path)

    def test_is_safe_predicate_returns_boolean_without_raising(self, tmp_path: Path) -> None:
        boundary = WorkspaceBoundary(tmp_path)
        valid_file = tmp_path / "valid.txt"
        valid_file.write_text("ok", encoding="utf-8")

        assert boundary.is_safe("valid.txt") is True
        assert boundary.is_safe("../../outside.txt") is False
        assert boundary.is_safe(".env") is False
        assert boundary.is_safe("sub/.git/HEAD") is False


# ===========================================================================
# 2. Policy Decisions
# ===========================================================================


class TestPolicyDecisions:
    """Verify policy classification (permit, deny, needs_approval, invalid_arguments)."""

    def test_policy_denies_unknown_action_kind(self, tmp_path: Path) -> None:
        engine = PolicyEngine(create_full_registry())
        boundary = WorkspaceBoundary(tmp_path)
        profile = get_workflow_profile(WorkflowType.UNDERSTAND)

        action = ProposedAction(kind="custom_actor_kind")
        decision = engine.evaluate(action, profile, boundary)
        assert decision.status == "deny"
        assert "unsupported action kind" in decision.reason.lower()

    def test_policy_flags_unregistered_tool_name(self, tmp_path: Path) -> None:
        engine = PolicyEngine(create_full_registry())
        boundary = WorkspaceBoundary(tmp_path)
        profile = get_workflow_profile(WorkflowType.GENERATE)

        action = ProposedAction(kind="tool_call", tool_name="format_c_drive")
        decision = engine.evaluate(action, profile, boundary)
        assert decision.status == "unknown_tool"
        assert "not registered" in decision.reason.lower()

    def test_policy_denies_write_tool_in_readonly_workflow(self, tmp_path: Path) -> None:
        engine = PolicyEngine(create_full_registry())
        boundary = WorkspaceBoundary(tmp_path)
        profile = get_workflow_profile(WorkflowType.UNDERSTAND)

        action = ProposedAction(
            kind="tool_call",
            tool_name="write_file",
            arguments={"path": "out.txt", "content": "data"},
        )
        decision = engine.evaluate(action, profile, boundary)
        assert decision.status == "deny"
        assert "prohibited in workflow 'understand'" in decision.reason

    def test_policy_flags_invalid_arguments_schema(self, tmp_path: Path) -> None:
        engine = PolicyEngine(create_full_registry())
        boundary = WorkspaceBoundary(tmp_path)
        profile = get_workflow_profile(WorkflowType.UNDERSTAND)

        # Missing required 'path' field
        action = ProposedAction(
            kind="tool_call",
            tool_name="read_file",
            arguments={"unknown_param": 123},
        )
        decision = engine.evaluate(action, profile, boundary)
        assert decision.status == "invalid_arguments"
        assert "schema validation" in decision.reason

    def test_policy_permits_read_action_in_understand_workflow(self, tmp_path: Path) -> None:
        engine = PolicyEngine(create_full_registry())
        boundary = WorkspaceBoundary(tmp_path)
        profile = get_workflow_profile(WorkflowType.UNDERSTAND)

        action = ProposedAction(
            kind="tool_call",
            tool_name="read_file",
            arguments={"path": "README.md"},
        )
        decision = engine.evaluate(action, profile, boundary)
        assert decision.status == "permit"
        assert decision.risk_level == RiskLevel.READ

    def test_policy_requires_approval_and_builds_diff_for_write_file(self, tmp_path: Path) -> None:
        engine = PolicyEngine(create_full_registry())
        boundary = WorkspaceBoundary(tmp_path)
        profile = get_workflow_profile(WorkflowType.GENERATE)

        target = tmp_path / "hello.py"
        target.write_text("old_line = 1\n", encoding="utf-8")

        action = ProposedAction(
            kind="tool_call",
            tool_name="write_file",
            arguments={"path": "hello.py", "content": "new_line = 2\n"},
        )
        decision = engine.evaluate(action, profile, boundary)
        assert decision.status == "needs_approval"
        assert decision.risk_level == RiskLevel.WRITE
        assert decision.target_path == "hello.py"
        assert decision.diff is not None
        assert "-old_line = 1" in decision.diff
        assert "+new_line = 2" in decision.diff

    def test_policy_requires_approval_and_captures_command_args(self, tmp_path: Path) -> None:
        engine = PolicyEngine(create_full_registry())
        boundary = WorkspaceBoundary(tmp_path)
        profile = get_workflow_profile(WorkflowType.TESTS)

        action = ProposedAction(
            kind="tool_call",
            tool_name="run_command",
            arguments={"command": ["pytest", "-v", "tests/"]},
        )
        decision = engine.evaluate(action, profile, boundary)
        assert decision.status == "needs_approval"
        assert decision.risk_level == RiskLevel.EXECUTE
        assert decision.command_args == ["pytest", "-v", "tests/"]


# ===========================================================================
# 3. Write and Shell Approval Requirements
# ===========================================================================


class TestWriteAndShellApprovalRequirements:
    """Verify mutating and executing tools require approval and respect user decision."""

    def test_write_file_approved_modifies_disk(self, tmp_path: Path) -> None:
        boundary = WorkspaceBoundary(tmp_path)
        registry = create_full_registry()

        fake_provider = FakeProvider(
            responses=[
                CompletionResponse(
                    tool_calls=[
                        ToolCall(
                            id="call_w1",
                            name="write_file",
                            arguments={"path": "created.txt", "content": "approved content\n"},
                        )
                    ],
                    finish_reason="tool_calls",
                ),
                CompletionResponse(content="File created successfully.", finish_reason="stop"),
            ]
        )

        runtime = AgentRuntime(
            provider=fake_provider,
            registry=registry,
            approval_handler=lambda _action, _decision: True,
        )
        req = AgentRequest(
            task="Create file",
            workspace=boundary,
            workflow=WorkflowType.GENERATE,
        )

        result = runtime.run(req)
        assert result.stop_reason == StopReason.COMPLETED
        assert (tmp_path / "created.txt").read_text(encoding="utf-8") == "approved content\n"
        assert "created.txt" in result.changed_files

    def test_write_file_denied_aborts_loop_without_disk_change(self, tmp_path: Path) -> None:
        boundary = WorkspaceBoundary(tmp_path)
        registry = create_full_registry()

        fake_provider = FakeProvider(
            responses=[
                CompletionResponse(
                    tool_calls=[
                        ToolCall(
                            id="call_w2",
                            name="write_file",
                            arguments={"path": "denied.txt", "content": "should not exist\n"},
                        )
                    ],
                    finish_reason="tool_calls",
                )
            ]
        )

        runtime = AgentRuntime(
            provider=fake_provider,
            registry=registry,
            approval_handler=lambda _action, _decision: False,
        )
        req = AgentRequest(
            task="Create file but user denies",
            workspace=boundary,
            workflow=WorkflowType.GENERATE,
        )

        result = runtime.run(req)
        assert result.stop_reason == StopReason.USER_CANCELLED
        assert not (tmp_path / "denied.txt").exists()
        assert len(result.changed_files) == 0

    def test_run_command_approved_executes_and_records_telemetry(self, tmp_path: Path) -> None:
        boundary = WorkspaceBoundary(tmp_path)
        registry = create_full_registry()

        fake_provider = FakeProvider(
            responses=[
                CompletionResponse(
                    tool_calls=[
                        ToolCall(
                            id="call_cmd1",
                            name="run_command",
                            arguments={"command": ["git", "--version"]},
                        )
                    ],
                    finish_reason="tool_calls",
                ),
                CompletionResponse(content="Git version verified.", finish_reason="stop"),
            ]
        )

        runtime = AgentRuntime(
            provider=fake_provider,
            registry=registry,
            approval_handler=lambda _action, _decision: True,
        )
        req = AgentRequest(
            task="Check git version",
            workspace=boundary,
            workflow=WorkflowType.GENERATE,
        )

        result = runtime.run(req)
        assert result.stop_reason == StopReason.COMPLETED
        assert result.run_summary is not None
        assert len(result.run_summary.commands_and_tests) == 1
        assert "git --version" in result.run_summary.commands_and_tests[0].command

    def test_run_command_denied_aborts_loop(self, tmp_path: Path) -> None:
        boundary = WorkspaceBoundary(tmp_path)
        registry = create_full_registry()

        fake_provider = FakeProvider(
            responses=[
                CompletionResponse(
                    tool_calls=[
                        ToolCall(
                            id="call_cmd2",
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
            registry=registry,
            approval_handler=lambda _action, _decision: False,
        )
        req = AgentRequest(
            task="Run pytest but user denies",
            workspace=boundary,
            workflow=WorkflowType.TESTS,
        )

        result = runtime.run(req)
        assert result.stop_reason == StopReason.USER_CANCELLED
        assert result.run_summary is not None
        assert len(result.run_summary.commands_and_tests) == 0

    def test_dry_run_mode_simulates_approval_without_modifying_disk(self, tmp_path: Path) -> None:
        boundary = WorkspaceBoundary(tmp_path)
        registry = create_full_registry()

        fake_provider = FakeProvider(
            responses=[
                CompletionResponse(
                    tool_calls=[
                        ToolCall(
                            id="call_dry",
                            name="write_file",
                            arguments={"path": "dry_run.txt", "content": "dry run simulation\n"},
                        )
                    ],
                    finish_reason="tool_calls",
                ),
                CompletionResponse(content="Simulated write complete.", finish_reason="stop"),
            ]
        )

        # In dry run, approval handler displays preview and returns False
        runtime = AgentRuntime(
            provider=fake_provider,
            registry=registry,
            approval_handler=make_cli_approval_handler(boundary, dry_run=True),
        )
        req = AgentRequest(
            task="Simulate writing file",
            workspace=boundary,
            workflow=WorkflowType.GENERATE,
        )

        result = runtime.run(req)
        assert result.stop_reason == StopReason.USER_CANCELLED
        assert not (tmp_path / "dry_run.txt").exists()


# ===========================================================================
# 4. Subprocess Timeout and Output Limits
# ===========================================================================


class TestSubprocessTimeoutAndOutputLimits:
    """Verify subprocess execution enforce hard limits on time and captured output."""

    def test_subprocess_timeout_enforcement(self, tmp_path: Path) -> None:
        boundary = WorkspaceBoundary(tmp_path)
        slow_script = tmp_path / "slow_runner.py"
        slow_script.write_text("import time; time.sleep(4)\n", encoding="utf-8")

        result = CommandRunner.run(
            argv=["python", "slow_runner.py"],
            cwd=boundary.root,
            workspace=boundary,
            timeout_seconds=1,
        )
        assert result.timed_out is True
        assert result.audit["status"] == "timed_out"

    def test_subprocess_caps_massive_stdout(self, tmp_path: Path) -> None:
        boundary = WorkspaceBoundary(tmp_path)
        spam_script = tmp_path / "spam_stdout.py"
        spam_script.write_text("print('A' * 20000)\n", encoding="utf-8")

        result = CommandRunner.run(
            argv=["python", "spam_stdout.py"],
            cwd=boundary.root,
            workspace=boundary,
            max_output_bytes=500,
        )
        assert result.truncated is True
        assert "stdout truncated" in result.stdout
        assert len(result.stdout) < 1500

    def test_subprocess_caps_massive_stderr(self, tmp_path: Path) -> None:
        boundary = WorkspaceBoundary(tmp_path)
        spam_script = tmp_path / "spam_stderr.py"
        spam_script.write_text(
            "import sys; sys.stderr.write('E' * 20000)\n",
            encoding="utf-8",
        )

        result = CommandRunner.run(
            argv=["python", "spam_stderr.py"],
            cwd=boundary.root,
            workspace=boundary,
            max_output_bytes=500,
        )
        assert result.truncated is True
        assert "stderr truncated" in result.stderr
        assert len(result.stderr) < 1500

    def test_subprocess_captures_nonzero_exit_code_and_duration(self, tmp_path: Path) -> None:
        boundary = WorkspaceBoundary(tmp_path)
        exit_script = tmp_path / "exit_err.py"
        exit_script.write_text("import sys; sys.exit(42)\n", encoding="utf-8")

        result = CommandRunner.run(
            argv=["python", "exit_err.py"],
            cwd=boundary.root,
            workspace=boundary,
        )
        assert result.exit_code == 42
        assert result.timed_out is False
        assert result.duration_ms > 0.0
        assert result.audit["status"] == "failed"

    def test_run_command_tool_propagates_timeout_as_error(self, tmp_path: Path) -> None:
        boundary = WorkspaceBoundary(tmp_path)
        slow_script = tmp_path / "slow_tool.py"
        slow_script.write_text("import time; time.sleep(3)\n", encoding="utf-8")

        tool = RunCommandTool()
        ctx = ToolContext(workspace=boundary, user_confirmed=True)

        result = tool.run({"command": ["python", "slow_tool.py"], "timeout_seconds": 1}, ctx)
        assert result.ok is False
        assert result.error is not None
        assert "timed out" in result.error.lower()


# ===========================================================================
# 5. Malformed Model Tool Calls
# ===========================================================================


class TestMalformedModelToolCalls:
    """Verify model errors trigger repairs, increment counters, and abort when exhausted."""

    def test_missing_required_arguments_triggers_repair_and_recovers(self, tmp_path: Path) -> None:
        readme = tmp_path / "README.md"
        readme.write_text("Valid project content\n", encoding="utf-8")

        boundary = WorkspaceBoundary(tmp_path)
        registry = create_full_registry()

        fake_provider = FakeProvider(
            responses=[
                # Turn 1: Malformed call (missing required 'path')
                CompletionResponse(
                    tool_calls=[ToolCall(id="c1", name="read_file", arguments={})],
                    finish_reason="tool_calls",
                ),
                # Turn 2: Repaired call
                CompletionResponse(
                    tool_calls=[
                        ToolCall(id="c2", name="read_file", arguments={"path": "README.md"})
                    ],
                    finish_reason="tool_calls",
                ),
                # Turn 3: Final answer
                CompletionResponse(content="Read succeeded on retry.", finish_reason="stop"),
            ]
        )

        runtime = AgentRuntime(provider=fake_provider, registry=registry)
        req = AgentRequest(
            task="Read readme",
            workspace=boundary,
            workflow=WorkflowType.UNDERSTAND,
        )

        result = runtime.run(req)
        assert result.stop_reason == StopReason.COMPLETED
        # Repairs counter was used and reset on successful execution
        assert result.repairs_used == 0

    def test_wrong_argument_types_triggers_repair(self, tmp_path: Path) -> None:
        boundary = WorkspaceBoundary(tmp_path)
        registry = create_full_registry()

        fake_provider = FakeProvider(
            responses=[
                # Invalid type: integer where string is required
                CompletionResponse(
                    tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": 12345})],
                    finish_reason="tool_calls",
                ),
                CompletionResponse(content="Corrected behavior.", finish_reason="stop"),
            ]
        )

        runtime = AgentRuntime(provider=fake_provider, registry=registry)
        req = AgentRequest(
            task="Read file with integer path",
            workspace=boundary,
            workflow=WorkflowType.UNDERSTAND,
        )

        result = runtime.run(req)
        assert result.stop_reason == StopReason.COMPLETED

    def test_consecutive_malformed_calls_abort_at_max_repairs(self, tmp_path: Path) -> None:
        boundary = WorkspaceBoundary(tmp_path)
        registry = create_full_registry()

        # 4 consecutive invalid calls with max_repairs=3
        fake_provider = FakeProvider(
            responses=[
                CompletionResponse(
                    tool_calls=[ToolCall(id=f"c{i}", name="read_file", arguments={})],
                    finish_reason="tool_calls",
                )
                for i in range(5)
            ]
        )

        runtime = AgentRuntime(provider=fake_provider, registry=registry)
        req = AgentRequest(
            task="Fail repeatedly",
            workspace=boundary,
            workflow=WorkflowType.UNDERSTAND,
            max_repairs=3,
        )

        result = runtime.run(req)
        assert result.stop_reason == StopReason.MAX_REPAIRS_REACHED
        assert result.repairs_used == 3

    def test_repair_counter_resets_after_valid_execution(self, tmp_path: Path) -> None:
        (tmp_path / "valid.txt").write_text("valid data\n", encoding="utf-8")
        boundary = WorkspaceBoundary(tmp_path)
        registry = create_full_registry()

        fake_provider = FakeProvider(
            responses=[
                # Turn 1: Malformed (repairs -> 1)
                CompletionResponse(
                    tool_calls=[ToolCall(id="c1", name="read_file", arguments={})],
                    finish_reason="tool_calls",
                ),
                # Turn 2: Malformed (repairs -> 2)
                CompletionResponse(
                    tool_calls=[ToolCall(id="c2", name="read_file", arguments={"path": 123})],
                    finish_reason="tool_calls",
                ),
                # Turn 3: Valid execution -> repairs must reset to 0
                CompletionResponse(
                    tool_calls=[
                        ToolCall(id="c3", name="read_file", arguments={"path": "valid.txt"})
                    ],
                    finish_reason="tool_calls",
                ),
                # Turn 4: Final response
                CompletionResponse(content="Done.", finish_reason="stop"),
            ]
        )

        runtime = AgentRuntime(provider=fake_provider, registry=registry)
        req = AgentRequest(
            task="Reset repairs test",
            workspace=boundary,
            workflow=WorkflowType.UNDERSTAND,
            max_repairs=3,
        )

        result = runtime.run(req)
        assert result.stop_reason == StopReason.COMPLETED
        assert result.repairs_used == 0


# ===========================================================================
# 6. Agent Loop Bounds
# ===========================================================================


class TestAgentLoopBounds:
    """Verify runtime enforces step budgets and stop reasons strictly."""

    def test_agent_loop_stops_strictly_at_max_steps(self, tmp_path: Path) -> None:
        (tmp_path / "file.txt").write_text("content\n", encoding="utf-8")
        boundary = WorkspaceBoundary(tmp_path)
        registry = create_full_registry()

        # Model attempts to make infinite tool calls
        fake_provider = FakeProvider(
            responses=[
                CompletionResponse(
                    tool_calls=[
                        ToolCall(id=f"c{i}", name="read_file", arguments={"path": "file.txt"})
                    ],
                    finish_reason="tool_calls",
                )
                for i in range(10)
            ]
        )

        runtime = AgentRuntime(provider=fake_provider, registry=registry)
        req = AgentRequest(
            task="Loop endlessly",
            workspace=boundary,
            workflow=WorkflowType.UNDERSTAND,
            max_steps=3,
        )

        result = runtime.run(req)
        assert result.stop_reason == StopReason.MAX_STEPS_REACHED
        assert result.steps_used == 3

    def test_agent_loop_respects_profile_default_budget(self, tmp_path: Path) -> None:
        (tmp_path / "doc.txt").write_text("doc\n", encoding="utf-8")
        boundary = WorkspaceBoundary(tmp_path)
        registry = create_full_registry()

        # UNDERSTAND profile default step budget is 8
        profile = get_workflow_profile(WorkflowType.UNDERSTAND)
        budget = profile.maximum_step_budget
        assert budget == 8

        fake_provider = FakeProvider(
            responses=[
                CompletionResponse(
                    tool_calls=[
                        ToolCall(id=f"call_{i}", name="read_file", arguments={"path": "doc.txt"})
                    ],
                    finish_reason="tool_calls",
                )
                for i in range(budget + 2)
            ]
        )

        runtime = AgentRuntime(provider=fake_provider, registry=registry)
        req = AgentRequest(
            task="Test profile step budget",
            workspace=boundary,
            workflow=WorkflowType.UNDERSTAND,
            max_steps=None,  # Use profile default
        )

        result = runtime.run(req)
        assert result.stop_reason == StopReason.MAX_STEPS_REACHED
        assert result.steps_used == 8

    def test_direct_completion_uses_zero_steps(self, tmp_path: Path) -> None:
        boundary = WorkspaceBoundary(tmp_path)
        registry = create_full_registry()

        fake_provider = FakeProvider(
            responses=[
                CompletionResponse(
                    content="Immediate answer without tools.",
                    finish_reason="stop",
                )
            ]
        )

        runtime = AgentRuntime(provider=fake_provider, registry=registry)
        req = AgentRequest(
            task="Direct question",
            workspace=boundary,
            workflow=WorkflowType.UNDERSTAND,
        )

        result = runtime.run(req)
        assert result.stop_reason == StopReason.COMPLETED
        assert result.steps_used == 0
        assert result.summary == "Immediate answer without tools."

    def test_stop_reasons_enum_integrity(self) -> None:
        expected = {
            "completed",
            "max_steps_reached",
            "max_repairs_reached",
            "user_cancelled",
            "policy_rejected",
            "error",
        }
        actual = {r.value for r in StopReason}
        assert actual == expected


# ===========================================================================
# 7. Provider Error Handling
# ===========================================================================


class TestProviderErrorHandling:
    """Verify provider failures are categorized into fatal vs recoverable and sanitized."""

    def test_unrecoverable_provider_auth_error_halts_immediately(self, tmp_path: Path) -> None:
        boundary = WorkspaceBoundary(tmp_path)
        registry = create_full_registry()

        fake_provider = FakeProvider(
            responses=[ProviderAuthError("Authentication failed: invalid api token")]
        )

        runtime = AgentRuntime(provider=fake_provider, registry=registry)
        req = AgentRequest(
            task="Query with invalid credentials",
            workspace=boundary,
            workflow=WorkflowType.UNDERSTAND,
        )

        result = runtime.run(req)
        assert result.stop_reason == StopReason.ERROR
        assert result.steps_used == 0
        assert "Authentication failed" in result.summary

    def test_transient_provider_error_recovers_gracefully(self, tmp_path: Path) -> None:
        boundary = WorkspaceBoundary(tmp_path)
        registry = create_full_registry()

        fake_provider = FakeProvider(
            responses=[
                ProviderRateLimitError("Rate limit exceeded 429"),
                CompletionResponse(
                    content="Recovered after transient rate limit.",
                    finish_reason="stop",
                ),
            ]
        )

        runtime = AgentRuntime(provider=fake_provider, registry=registry)
        req = AgentRequest(
            task="Query with transient rate limit",
            workspace=boundary,
            workflow=WorkflowType.UNDERSTAND,
            max_repairs=3,
        )

        result = runtime.run(req)
        assert result.stop_reason == StopReason.COMPLETED
        assert "Recovered after transient rate limit" in result.summary

    def test_provider_error_message_scrubs_secrets_in_result(self, tmp_path: Path) -> None:
        boundary = WorkspaceBoundary(tmp_path)
        registry = create_full_registry()

        raw_secret = "sk-proj-leaked_openai_secret_token_123456"
        fake_provider = FakeProvider(
            responses=[ProviderAuthError(f"Rejected key {raw_secret} for model gpt-4o")]
        )

        runtime = AgentRuntime(provider=fake_provider, registry=registry)
        req = AgentRequest(
            task="Scrub auth secret",
            workspace=boundary,
            workflow=WorkflowType.UNDERSTAND,
        )

        result = runtime.run(req)
        assert raw_secret not in result.summary
        assert "[REDACTED]" in result.summary


# ===========================================================================
# 8. Secret Redaction
# ===========================================================================


class TestSecretRedaction:
    """Verify secrets and authorization tokens are redacted across all layers."""

    @pytest.mark.parametrize(
        ("raw_input", "expected_fragment"),
        [
            ("Bearer token_abcdef12345678", "Bearer [REDACTED]"),
            ("bearer token_abcdef12345678", "bearer [REDACTED]"),
            ("BEARER token_abcdef12345678", "BEARER [REDACTED]"),
            ("Using key sk-1234567890abcdef1234 for LLM", "Using key [REDACTED] for LLM"),
            ("Using key sk-proj-1234567890abcdef for LLM", "Using key [REDACTED] for LLM"),
            (
                "Gemini key AIzaSyD-1234567890abcdef123456789012345 used",
                "Gemini key [REDACTED] used",
            ),
            ("https://api.test.com?api_key=secret_12345678", "?api_key=[REDACTED]"),
            ("Token: token-1234567890abcdef", "Token: [REDACTED]"),
            ("Secret: secret-1234567890abcdef", "Secret: [REDACTED]"),
        ],
    )
    def test_redact_secrets_patterns(self, raw_input: str, expected_fragment: str) -> None:
        sanitized = redact_secrets(raw_input)
        assert expected_fragment in sanitized

    def test_redact_headers_masks_sensitive_keys(self) -> None:
        headers = {
            "Authorization": "Bearer my_super_secret_token_12345",
            "x-api-key": "secret_key_abc123",
            "Content-Type": "application/json",
        }
        sanitized = redact_headers(headers)
        assert sanitized["Authorization"] == "Bearer [REDACTED]"
        assert sanitized["x-api-key"] == "[REDACTED]"
        assert sanitized["Content-Type"] == "application/json"

    def test_end_to_end_secret_scrubbing_in_transcript_and_summary(self, tmp_path: Path) -> None:
        boundary = WorkspaceBoundary(tmp_path)
        registry = create_full_registry()

        secret_key = "sk-proj-testleakedkey1234567890"
        bearer_token = "Bearer authorization_secret_9988776655"

        fake_provider = FakeProvider(
            responses=[
                CompletionResponse(
                    content=f"Processed with key {secret_key} and {bearer_token}.",
                    finish_reason="stop",
                )
            ]
        )

        runtime = AgentRuntime(provider=fake_provider, registry=registry)
        req = AgentRequest(
            task="Ensure secrets are redacted from disk files",
            workspace=boundary,
            workflow=WorkflowType.UNDERSTAND,
        )

        result = runtime.run(req)
        assert result.stop_reason == StopReason.COMPLETED
        assert result.transcript_path is not None
        assert result.transcript_path.exists()

        # Transcript must not contain raw secret
        transcript_text = result.transcript_path.read_text(encoding="utf-8")
        assert secret_key not in transcript_text
        assert "authorization_secret_9988776655" not in transcript_text

        # Summary must not contain raw secret
        memory = LocalMemory(boundary)
        assert result.run_id is not None
        summary = memory.get_summary(result.run_id)
        assert secret_key not in summary.summary
        assert "authorization_secret_9988776655" not in summary.summary


# ===========================================================================
# 9. Workflow Tool Restrictions
# ===========================================================================


class TestWorkflowToolRestrictions:
    """Verify tool allowlists and risk profiles are enforced deterministically."""

    def test_understand_profile_excludes_mutating_tools_from_declarations(
        self, tmp_path: Path
    ) -> None:
        boundary = WorkspaceBoundary(tmp_path)
        registry = create_full_registry()

        fake_provider = FakeProvider(
            responses=[CompletionResponse(content="Understood layout.", finish_reason="stop")]
        )

        runtime = AgentRuntime(provider=fake_provider, registry=registry)
        req = AgentRequest(
            task="Inspect project",
            workspace=boundary,
            workflow=WorkflowType.UNDERSTAND,
        )

        runtime.run(req)
        assert len(fake_provider.calls) == 1
        declared_tools = {t.name for t in fake_provider.calls[0].tools}

        assert declared_tools == {"list_files", "read_file", "search_text"}
        assert "write_file" not in declared_tools
        assert "apply_patch" not in declared_tools
        assert "run_command" not in declared_tools

    def test_review_profile_excludes_mutating_tools_from_declarations(self, tmp_path: Path) -> None:
        boundary = WorkspaceBoundary(tmp_path)
        registry = create_full_registry()

        fake_provider = FakeProvider(
            responses=[CompletionResponse(content="Review complete.", finish_reason="stop")]
        )

        runtime = AgentRuntime(provider=fake_provider, registry=registry)
        req = AgentRequest(
            task="Review project",
            workspace=boundary,
            workflow=WorkflowType.REVIEW,
        )

        runtime.run(req)
        assert len(fake_provider.calls) == 1
        declared_tools = {t.name for t in fake_provider.calls[0].tools}

        assert declared_tools == {"list_files", "read_file", "search_text"}
        assert "write_file" not in declared_tools
        assert "apply_patch" not in declared_tools
        assert "run_command" not in declared_tools

    def test_policy_rejects_model_attempting_write_in_understand_workflow(
        self, tmp_path: Path
    ) -> None:
        boundary = WorkspaceBoundary(tmp_path)
        registry = create_full_registry()

        # Model adversarial proposal
        fake_provider = FakeProvider(
            responses=[
                CompletionResponse(
                    tool_calls=[
                        ToolCall(
                            id="hack_write",
                            name="write_file",
                            arguments={"path": "pwned.txt", "content": "bad"},
                        )
                    ],
                    finish_reason="tool_calls",
                )
            ]
        )

        runtime = AgentRuntime(provider=fake_provider, registry=registry)
        req = AgentRequest(
            task="Try writing in understand profile",
            workspace=boundary,
            workflow=WorkflowType.UNDERSTAND,
        )

        result = runtime.run(req)
        assert result.stop_reason == StopReason.POLICY_REJECTED
        assert not (tmp_path / "pwned.txt").exists()

    def test_policy_rejects_model_attempting_command_in_review_workflow(
        self, tmp_path: Path
    ) -> None:
        boundary = WorkspaceBoundary(tmp_path)
        registry = create_full_registry()

        # Model adversarial proposal
        fake_provider = FakeProvider(
            responses=[
                CompletionResponse(
                    tool_calls=[
                        ToolCall(
                            id="hack_cmd",
                            name="run_command",
                            arguments={"command": ["pytest"]},
                        )
                    ],
                    finish_reason="tool_calls",
                )
            ]
        )

        runtime = AgentRuntime(provider=fake_provider, registry=registry)
        req = AgentRequest(
            task="Try command in review profile",
            workspace=boundary,
            workflow=WorkflowType.REVIEW,
        )

        result = runtime.run(req)
        assert result.stop_reason == StopReason.POLICY_REJECTED


# ===========================================================================
# 10. Final Transcript and Summary Correctness
# ===========================================================================


class TestFinalTranscriptAndSummaryCorrectness:
    """Verify all events and audit records are structured, persisted, and retrievable."""

    def test_run_summary_contains_complete_audit_telemetry(self, tmp_path: Path) -> None:
        boundary = WorkspaceBoundary(tmp_path)
        registry = create_full_registry()

        fake_provider = FakeProvider(
            responses=[
                # Turn 1: write a file
                CompletionResponse(
                    tool_calls=[
                        ToolCall(
                            id="c1",
                            name="write_file",
                            arguments={
                                "path": "calc.py",
                                "content": "def add(a, b): return a + b\n",
                            },
                        )
                    ],
                    finish_reason="tool_calls",
                ),
                # Turn 2: run a command
                CompletionResponse(
                    tool_calls=[
                        ToolCall(
                            id="c2",
                            name="run_command",
                            arguments={"command": ["python", "-m", "pytest", "--version"]},
                        )
                    ],
                    finish_reason="tool_calls",
                ),
                # Turn 3: completed
                CompletionResponse(content="Feature implemented and tested.", finish_reason="stop"),
            ]
        )

        runtime = AgentRuntime(
            provider=fake_provider,
            registry=registry,
            approval_handler=lambda _action, _decision: True,
        )
        req = AgentRequest(
            task="Build add function and test",
            workspace=boundary,
            workflow=WorkflowType.GENERATE,
        )

        result = runtime.run(req)
        assert result.stop_reason == StopReason.COMPLETED
        assert result.run_summary is not None

        summary = result.run_summary
        assert summary.run_id == result.run_id
        assert summary.task_objective == "Build add function and test"
        assert summary.workflow == "generate"
        assert summary.final_stop_reason == StopReason.COMPLETED
        assert summary.changed_files == ["calc.py"]
        assert len(summary.approved_actions) == 2
        assert len(summary.commands_and_tests) == 1
        assert summary.commands_and_tests[0].is_test is True

    def test_transcript_jsonl_events_are_valid_and_chronological(self, tmp_path: Path) -> None:
        (tmp_path / "info.txt").write_text("sample info\n", encoding="utf-8")
        boundary = WorkspaceBoundary(tmp_path)
        registry = create_full_registry()

        fake_provider = FakeProvider(
            responses=[
                CompletionResponse(
                    tool_calls=[
                        ToolCall(id="c1", name="read_file", arguments={"path": "info.txt"})
                    ],
                    finish_reason="tool_calls",
                ),
                CompletionResponse(content="Read finished.", finish_reason="stop"),
            ]
        )

        runtime = AgentRuntime(provider=fake_provider, registry=registry)
        req = AgentRequest(
            task="Check transcript JSONL formatting",
            workspace=boundary,
            workflow=WorkflowType.UNDERSTAND,
        )

        result = runtime.run(req)
        assert result.transcript_path is not None
        assert result.transcript_path.exists()

        events: list[dict[str, Any]] = []
        with result.transcript_path.open("r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped:
                    event = json.loads(stripped)
                    events.append(event)

        event_names = [e["event"] for e in events]
        assert "run_start" in event_names
        assert "proposed_action" in event_names
        assert "policy_evaluation" in event_names
        assert "tool_execution" in event_names
        assert "run_stop" in event_names

        # First event is run_start, last is run_stop
        assert event_names[0] == "run_start"
        assert event_names[-1] == "run_stop"

    def test_unresolved_issues_recorded_when_policy_denies_action(self, tmp_path: Path) -> None:
        boundary = WorkspaceBoundary(tmp_path)
        registry = create_full_registry()

        fake_provider = FakeProvider(
            responses=[
                CompletionResponse(
                    tool_calls=[
                        ToolCall(
                            id="c_illegal",
                            name="write_file",
                            arguments={"path": "illegal.txt", "content": "nope"},
                        )
                    ],
                    finish_reason="tool_calls",
                )
            ]
        )

        runtime = AgentRuntime(provider=fake_provider, registry=registry)
        req = AgentRequest(
            task="Trigger unresolved issue",
            workspace=boundary,
            workflow=WorkflowType.UNDERSTAND,
        )

        result = runtime.run(req)
        assert result.stop_reason == StopReason.POLICY_REJECTED
        assert result.run_summary is not None
        assert len(result.run_summary.unresolved_issues) > 0
        assert "prohibited" in result.run_summary.unresolved_issues[0].lower()
