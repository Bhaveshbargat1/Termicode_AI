"""Integration tests for the TermiCode agent loop using FakeProvider and ToolRegistry."""

from pathlib import Path

from termicode.agent import (
    AgentRequest,
    AgentRuntime,
    StopReason,
)
from termicode.agent.workflows import WorkflowType
from termicode.providers import (
    CompletionResponse,
    FakeProvider,
    ToolCall,
)
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


def create_full_tool_registry() -> ToolRegistry:
    """Create a registry containing all core MVP tools."""
    registry = ToolRegistry()
    registry.register(ListFilesTool())
    registry.register(ReadFileTool())
    registry.register(SearchTextTool())
    registry.register(WriteFileTool())
    registry.register(ApplyPatchTool())
    registry.register(RunCommandTool())
    return registry


# --- 1. Success Integration Test ---


def test_agent_loop_success_read_and_complete(tmp_path: Path) -> None:
    # Setup test file
    readme = tmp_path / "README.md"
    readme.write_text("# TermiCode Core\nSafe coding assistant.\n", encoding="utf-8")

    boundary = WorkspaceBoundary(tmp_path)
    registry = create_full_tool_registry()

    # Step 1: Model proposes reading README.md
    # Step 2: Model finishes with final explanation
    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="call_read_1",
                        name="read_file",
                        arguments={"path": "README.md"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            CompletionResponse(
                content="The project is TermiCode Core, a safe coding assistant.",
                finish_reason="stop",
            ),
        ]
    )

    runtime = AgentRuntime(provider=fake_provider, registry=registry)
    req = AgentRequest(
        task="Explain what this repository is about.",
        workspace=boundary,
        workflow=WorkflowType.UNDERSTAND,
    )

    result = runtime.run(req)

    assert result.stop_reason == StopReason.COMPLETED
    assert result.steps_used == 1
    assert result.repairs_used == 0
    assert "TermiCode Core" in result.summary
    assert result.transcript_path is not None
    assert result.transcript_path.exists()
    assert result.transcript_path.stat().st_size > 0


# --- 2. Denied Approval Test ---


def test_agent_loop_denied_approval_stops_run(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    registry = create_full_tool_registry()

    # Model proposes writing a file in GENERATE workflow
    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="call_write_1",
                        name="write_file",
                        arguments={"path": "src/danger.py", "content": "import os\n"},
                    )
                ],
                finish_reason="tool_calls",
            )
        ]
    )

    # Approval handler explicitly denies approval
    approval_called = False

    def deny_handler(_action: object, _decision: object) -> bool:
        nonlocal approval_called
        approval_called = True
        return False

    runtime = AgentRuntime(
        provider=fake_provider,
        registry=registry,
        approval_handler=deny_handler,
    )

    req = AgentRequest(
        task="Create danger.py",
        workspace=boundary,
        workflow=WorkflowType.GENERATE,
    )

    result = runtime.run(req)

    assert approval_called is True
    assert result.stop_reason == StopReason.USER_CANCELLED
    assert result.steps_used == 1
    assert result.changed_files == []
    # Verify file was never written to disk
    assert not (tmp_path / "src" / "danger.py").exists()


# --- 3. Malformed Tool Call Test (Recovery) ---


def test_agent_loop_malformed_tool_call_and_recovery(tmp_path: Path) -> None:
    (tmp_path / "valid.txt").write_text("Hello valid file", encoding="utf-8")
    boundary = WorkspaceBoundary(tmp_path)
    registry = create_full_tool_registry()

    # Step 1: Model proposes malformed call (missing required 'path' field)
    # Step 2: Model receives validation error and corrects call
    # Step 3: Model finishes
    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="call_bad",
                        name="read_file",
                        arguments={},  # Missing required 'path'
                    )
                ],
                finish_reason="tool_calls",
            ),
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="call_good",
                        name="read_file",
                        arguments={"path": "valid.txt"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            CompletionResponse(
                content="I recovered and read valid.txt successfully.",
                finish_reason="stop",
            ),
        ]
    )

    runtime = AgentRuntime(provider=fake_provider, registry=registry)
    req = AgentRequest(
        task="Read valid.txt",
        workspace=boundary,
        workflow=WorkflowType.UNDERSTAND,
    )

    result = runtime.run(req)

    assert result.stop_reason == StopReason.COMPLETED
    assert result.steps_used == 2
    assert result.repairs_used == 0  # Successful call reset consecutive repair counter
    assert "recovered and read" in result.summary


# --- 4. Repeated Loop Tests ---


def test_agent_loop_max_steps_reached(tmp_path: Path) -> None:
    (tmp_path / "test.txt").write_text("content", encoding="utf-8")
    boundary = WorkspaceBoundary(tmp_path)
    registry = create_full_tool_registry()

    # Provider always returns tool calls, never a final answer
    fake_provider = FakeProvider(
        default_response=CompletionResponse(
            tool_calls=[
                ToolCall(
                    id="call_loop",
                    name="read_file",
                    arguments={"path": "test.txt"},
                )
            ],
            finish_reason="tool_calls",
        )
    )

    runtime = AgentRuntime(provider=fake_provider, registry=registry)
    req = AgentRequest(
        task="Endless task",
        workspace=boundary,
        workflow=WorkflowType.UNDERSTAND,
        max_steps=5,  # Test with budget of 5
    )

    result = runtime.run(req)

    assert result.stop_reason == StopReason.MAX_STEPS_REACHED
    assert result.steps_used == 5


def test_agent_loop_max_repairs_reached(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    registry = create_full_tool_registry()

    # Provider continuously calls a nonexistent tool
    fake_provider = FakeProvider(
        default_response=CompletionResponse(
            tool_calls=[
                ToolCall(
                    id="call_fail",
                    name="nonexistent_tool_xyz",
                    arguments={},
                )
            ],
            finish_reason="tool_calls",
        )
    )

    runtime = AgentRuntime(provider=fake_provider, registry=registry)
    req = AgentRequest(
        task="Failing task",
        workspace=boundary,
        workflow=WorkflowType.UNDERSTAND,
        max_repairs=3,
    )

    result = runtime.run(req)

    assert result.stop_reason == StopReason.MAX_REPAIRS_REACHED
    assert result.repairs_used == 3


# --- 5. Tool Failure Test ---


def test_agent_loop_tool_failure_feedback(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    registry = create_full_tool_registry()

    # Step 1: Model calls read_file on nonexistent file (fails)
    # Step 2: Model recognizes failure and concludes
    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="call_fail",
                        name="read_file",
                        arguments={"path": "missing_file.py"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            CompletionResponse(
                content="The requested file missing_file.py does not exist.",
                finish_reason="stop",
            ),
        ]
    )

    runtime = AgentRuntime(provider=fake_provider, registry=registry)
    req = AgentRequest(
        task="Check missing file",
        workspace=boundary,
        workflow=WorkflowType.UNDERSTAND,
    )

    result = runtime.run(req)

    assert result.stop_reason == StopReason.COMPLETED
    assert result.steps_used == 1
    assert "missing_file.py does not exist" in result.summary


# --- 6. Policy Rejection Test ---


def test_agent_loop_policy_rejection_in_readonly_workflow(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    registry = create_full_tool_registry()

    # Model in read-only UNDERSTAND workflow attempts to execute a write tool
    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="call_write_illegal",
                        name="write_file",
                        arguments={"path": "src/illegal.py", "content": "x = 1"},
                    )
                ],
                finish_reason="tool_calls",
            )
        ]
    )

    runtime = AgentRuntime(provider=fake_provider, registry=registry)
    req = AgentRequest(
        task="Try to write file in read-only workflow",
        workspace=boundary,
        workflow=WorkflowType.UNDERSTAND,
    )

    result = runtime.run(req)

    # Hard boundary: PolicyEngine rejects the write before it can run or ask for approval
    assert result.stop_reason == StopReason.POLICY_REJECTED
    assert result.steps_used == 1
    assert result.changed_files == []
    assert not (tmp_path / "src" / "illegal.py").exists()


# --- 7. Full Write Approval Execution Test ---


def test_agent_loop_approved_write_modifies_disk(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    registry = create_full_tool_registry()

    # Step 1: Model proposes write in GENERATE workflow
    # Step 2: Model concludes
    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="call_write_approved",
                        name="write_file",
                        arguments={"path": "src/created.py", "content": "print('created')\n"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            CompletionResponse(
                content="Successfully created src/created.py.",
                finish_reason="stop",
            ),
        ]
    )

    # Approval handler approves
    def approve_handler(_action: object, _decision: object) -> bool:
        return True

    runtime = AgentRuntime(
        provider=fake_provider,
        registry=registry,
        approval_handler=approve_handler,
    )

    req = AgentRequest(
        task="Create created.py",
        workspace=boundary,
        workflow=WorkflowType.GENERATE,
    )

    result = runtime.run(req)

    assert result.stop_reason == StopReason.COMPLETED
    assert result.steps_used == 1
    assert result.changed_files == ["src/created.py"]
    assert (tmp_path / "src" / "created.py").exists()
    assert (tmp_path / "src" / "created.py").read_text(encoding="utf-8") == "print('created')\n"


# --- 8. Shell Command Approval Flow ---


def test_agent_loop_shell_command_denied(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    registry = create_full_tool_registry()

    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="call_cmd_1",
                        name="run_command",
                        arguments={"command": ["pytest", "--version"]},
                    )
                ],
                finish_reason="tool_calls",
            )
        ]
    )

    # Deny approval
    runtime = AgentRuntime(
        provider=fake_provider,
        registry=registry,
        approval_handler=lambda _a, _d: False,
    )

    req = AgentRequest(
        task="Run test suite",
        workspace=boundary,
        workflow=WorkflowType.GENERATE,
    )

    result = runtime.run(req)

    assert result.stop_reason == StopReason.USER_CANCELLED
    assert result.steps_used == 1
    assert result.commands_run == []


def test_agent_loop_shell_command_approved(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    registry = create_full_tool_registry()

    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="call_cmd_ok",
                        name="run_command",
                        arguments={"command": ["pytest", "--version"]},
                    )
                ],
                finish_reason="tool_calls",
            ),
            CompletionResponse(
                content="Pytest verified successfully.",
                finish_reason="stop",
            ),
        ]
    )

    # Grant approval
    runtime = AgentRuntime(
        provider=fake_provider,
        registry=registry,
        approval_handler=lambda _a, _d: True,
    )

    req = AgentRequest(
        task="Run test suite",
        workspace=boundary,
        workflow=WorkflowType.GENERATE,
    )

    result = runtime.run(req)

    assert result.stop_reason == StopReason.COMPLETED
    assert result.steps_used == 1
    assert len(result.commands_run) == 1
    assert "pytest --version" in result.commands_run[0]
    assert len(result.tests_run) == 1


# --- 9. Patch Approval Flow ---


def test_agent_loop_patch_denied(tmp_path: Path) -> None:
    code_file = tmp_path / "app.py"
    code_file.write_text("a = 1\nb = 2\n", encoding="utf-8")

    boundary = WorkspaceBoundary(tmp_path)
    registry = create_full_tool_registry()

    patch_text = "@@ -1,2 +1,2 @@\n a = 1\n-b = 2\n+b = 20\n"
    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="call_patch_1",
                        name="apply_patch",
                        arguments={"path": "app.py", "patch": patch_text},
                    )
                ],
                finish_reason="tool_calls",
            )
        ]
    )

    # Deny approval
    runtime = AgentRuntime(
        provider=fake_provider,
        registry=registry,
        approval_handler=lambda _a, _d: False,
    )

    req = AgentRequest(
        task="Apply patch to app.py",
        workspace=boundary,
        workflow=WorkflowType.DEBUG,
    )

    result = runtime.run(req)

    assert result.stop_reason == StopReason.USER_CANCELLED
    assert result.changed_files == []
    # Disk remains unaltered
    assert code_file.read_text(encoding="utf-8") == "a = 1\nb = 2\n"


def test_agent_loop_patch_approved(tmp_path: Path) -> None:
    code_file = tmp_path / "app.py"
    code_file.write_text("a = 1\nb = 2\n", encoding="utf-8")

    boundary = WorkspaceBoundary(tmp_path)
    registry = create_full_tool_registry()

    patch_text = "@@ -1,2 +1,2 @@\n a = 1\n-b = 2\n+b = 20\n"
    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="call_patch_ok",
                        name="apply_patch",
                        arguments={"path": "app.py", "patch": patch_text},
                    )
                ],
                finish_reason="tool_calls",
            ),
            CompletionResponse(
                content="Patch applied successfully.",
                finish_reason="stop",
            ),
        ]
    )

    runtime = AgentRuntime(
        provider=fake_provider,
        registry=registry,
        approval_handler=lambda _a, _d: True,
    )

    req = AgentRequest(
        task="Apply patch to app.py",
        workspace=boundary,
        workflow=WorkflowType.DEBUG,
    )

    result = runtime.run(req)

    assert result.stop_reason == StopReason.COMPLETED
    assert result.changed_files == ["app.py"]
    assert code_file.read_text(encoding="utf-8") == "a = 1\nb = 20\n"


# --- 10. Consecutive Repair Reset on Success ---


def test_agent_loop_repair_counter_resets_on_success(tmp_path: Path) -> None:
    (tmp_path / "file1.txt").write_text("content 1", encoding="utf-8")
    (tmp_path / "file2.txt").write_text("content 2", encoding="utf-8")

    boundary = WorkspaceBoundary(tmp_path)
    registry = create_full_tool_registry()

    # Sequence: Fail -> Success -> Fail -> Success -> Complete
    # Even though total failures = 2, max consecutive repairs is 2.
    # Because each failure was followed by a success, repairs_used resets to 0.
    fake_provider = FakeProvider(
        responses=[
            # 1. Fail: nonexistent file
            CompletionResponse(
                tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "ghost1.txt"})],
                finish_reason="tool_calls",
            ),
            # 2. Success: valid file1.txt (resets repairs_used to 0)
            CompletionResponse(
                tool_calls=[ToolCall(id="c2", name="read_file", arguments={"path": "file1.txt"})],
                finish_reason="tool_calls",
            ),
            # 3. Fail: nonexistent file again
            CompletionResponse(
                tool_calls=[ToolCall(id="c3", name="read_file", arguments={"path": "ghost2.txt"})],
                finish_reason="tool_calls",
            ),
            # 4. Success: valid file2.txt (resets repairs_used to 0)
            CompletionResponse(
                tool_calls=[ToolCall(id="c4", name="read_file", arguments={"path": "file2.txt"})],
                finish_reason="tool_calls",
            ),
            # 5. Complete
            CompletionResponse(
                content="Completed multi-step inspection successfully.",
                finish_reason="stop",
            ),
        ]
    )

    runtime = AgentRuntime(provider=fake_provider, registry=registry)
    req = AgentRequest(
        task="Inspect with intermittent recovery",
        workspace=boundary,
        workflow=WorkflowType.UNDERSTAND,
        max_repairs=2,  # Budget of 2 consecutive repairs
    )

    result = runtime.run(req)

    assert result.stop_reason == StopReason.COMPLETED
    assert result.steps_used == 4
    assert result.repairs_used == 0


# --- 11. Provider Error Handling in Agent Loop ---


def test_agent_loop_unrecoverable_provider_auth_error(tmp_path: Path) -> None:
    from termicode.providers import ProviderAuthError

    boundary = WorkspaceBoundary(tmp_path)
    registry = create_full_tool_registry()

    fake_provider = FakeProvider(responses=[ProviderAuthError("Invalid API key credentials")])

    runtime = AgentRuntime(provider=fake_provider, registry=registry)
    req = AgentRequest(
        task="Run task with bad auth",
        workspace=boundary,
        workflow=WorkflowType.UNDERSTAND,
    )

    result = runtime.run(req)

    assert result.stop_reason == StopReason.ERROR
    assert "Invalid API key credentials" in result.summary


def test_agent_loop_recoverable_provider_error_recovery(tmp_path: Path) -> None:
    from termicode.providers import ProviderRateLimitError

    boundary = WorkspaceBoundary(tmp_path)
    registry = create_full_tool_registry()

    # First turn: rate limit error; Second turn: succeeds with answer
    fake_provider = FakeProvider(
        responses=[
            ProviderRateLimitError("Rate limit reached: 429"),
            CompletionResponse(content="Answer delivered after retry.", finish_reason="stop"),
        ]
    )

    runtime = AgentRuntime(provider=fake_provider, registry=registry)
    req = AgentRequest(
        task="Run task with transient error",
        workspace=boundary,
        workflow=WorkflowType.UNDERSTAND,
        max_repairs=3,
    )

    result = runtime.run(req)

    assert result.stop_reason == StopReason.COMPLETED
    assert "Answer delivered after retry" in result.summary


# --- 12. Direct Completion (Zero Steps) ---


def test_agent_loop_direct_completion_zero_steps(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    registry = create_full_tool_registry()

    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                content="Direct answer without any tool calls.",
                finish_reason="stop",
            )
        ]
    )

    runtime = AgentRuntime(provider=fake_provider, registry=registry)
    req = AgentRequest(
        task="Explain something directly",
        workspace=boundary,
        workflow=WorkflowType.UNDERSTAND,
    )

    result = runtime.run(req)

    assert result.stop_reason == StopReason.COMPLETED
    assert result.steps_used == 0
    assert result.summary == "Direct answer without any tool calls."


# --- 13. Transcript Correctness and Secret Redaction ---


def test_agent_loop_transcript_audit_and_secret_redaction(tmp_path: Path) -> None:
    import json

    (tmp_path / "token.txt").write_text("sample content", encoding="utf-8")
    boundary = WorkspaceBoundary(tmp_path)
    registry = create_full_tool_registry()

    secret_key = "sk-secret-leaked-key-123456789"
    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="read_file",
                        arguments={"path": "token.txt"},
                        rationale=f"Reading using key {secret_key}",
                    )
                ],
                finish_reason="tool_calls",
            ),
            CompletionResponse(
                content=f"Done. Discovered credential {secret_key}",
                finish_reason="stop",
            ),
        ]
    )

    runtime = AgentRuntime(provider=fake_provider, registry=registry)
    req = AgentRequest(
        task=f"Task containing credential {secret_key}",
        workspace=boundary,
        workflow=WorkflowType.UNDERSTAND,
    )

    result = runtime.run(req)

    assert result.transcript_path is not None
    assert result.transcript_path.exists()

    # Read all lines from transcript JSONL
    lines = result.transcript_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 3

    # Verify every line is valid JSON and that secret_key is REDACTED everywhere
    full_transcript_text = "\n".join(lines)
    assert secret_key not in full_transcript_text
    assert "[REDACTED]" in full_transcript_text

    events = [json.loads(line) for line in lines]
    event_names = [e["event"] for e in events]
    assert "run_start" in event_names
    assert "proposed_action" in event_names
    assert "run_stop" in event_names


# --- 14. Workspace Escape Attempt via Agent Loop ---


def test_agent_loop_workspace_escape_attempt_rejected(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    registry = create_full_tool_registry()

    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="c_esc",
                        name="write_file",
                        arguments={"path": "../../escaped.txt", "content": "evil"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            CompletionResponse(
                content="Understood that escape was rejected.",
                finish_reason="stop",
            ),
        ]
    )

    runtime = AgentRuntime(
        provider=fake_provider,
        registry=registry,
        approval_handler=lambda _a, _d: True,
    )

    req = AgentRequest(
        task="Try to write outside",
        workspace=boundary,
        workflow=WorkflowType.GENERATE,
    )

    result = runtime.run(req)

    # Tool rejects workspace escape -> feedback given to model -> model completes
    assert result.stop_reason == StopReason.COMPLETED
    assert not (tmp_path.parent / "escaped.txt").exists()


# --- 15. Tool Output Truncation in Agent Messages ---


def test_agent_loop_tool_output_truncation(tmp_path: Path) -> None:
    # Large file > 16,384 characters
    big_file = tmp_path / "huge.txt"
    big_file.write_text("Z" * 30000, encoding="utf-8")

    boundary = WorkspaceBoundary(tmp_path)
    registry = create_full_tool_registry()

    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="c_big",
                        name="read_file",
                        arguments={"path": "huge.txt", "max_bytes": 25000},
                    )
                ],
                finish_reason="tool_calls",
            ),
            CompletionResponse(
                content="Inspected large file successfully.",
                finish_reason="stop",
            ),
        ]
    )

    runtime = AgentRuntime(provider=fake_provider, registry=registry)
    req = AgentRequest(
        task="Read huge file",
        workspace=boundary,
        workflow=WorkflowType.UNDERSTAND,
    )

    result = runtime.run(req)

    assert result.stop_reason == StopReason.COMPLETED
    assert result.steps_used == 1
