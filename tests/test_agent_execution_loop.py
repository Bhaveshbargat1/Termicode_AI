"""Integration tests for the Agent Execution Loop with Local, RAG, and MCP Tools."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from termicode.agent.models import AgentRequest, ProposedAction, StopReason
from termicode.agent.runtime import AgentRuntime
from termicode.agent.workflows import WorkflowType
from termicode.cli import app
from termicode.providers.base import (
    CompletionResponse,
    ProviderAuthError,
    ToolCall,
)
from termicode.providers.fake import FakeProvider
from termicode.tools.command import RunCommandTool
from termicode.tools.filesystem import (
    ApplyPatchTool,
    ListFilesTool,
    ReadFileTool,
    SearchTextTool,
    WriteFileTool,
)
from termicode.tools.rag import RAGRetrieveTool
from termicode.tools.registry import ToolRegistry
from termicode.tools.toolbridge import (
    MCPTool,
    ToolBridgeClient,
    ToolBridgeConnectionError,
)
from termicode.workspace import WorkspaceBoundary

runner = CliRunner()


def build_test_registry() -> ToolRegistry:
    """Build a complete tool registry including filesystem, command, and RAG tools."""
    registry = ToolRegistry()
    registry.register(ListFilesTool())
    registry.register(ReadFileTool())
    registry.register(SearchTextTool())
    registry.register(WriteFileTool())
    registry.register(ApplyPatchTool())
    registry.register(RunCommandTool())
    registry.register(RAGRetrieveTool())
    return registry


# ==============================================================================
# 1. Local Scenario: Create file and show it
# ==============================================================================


def test_scenario_create_and_show_file(tmp_path: Path) -> None:
    """Test multi-step execution: create file a.txt, read/show it, and complete."""
    boundary = WorkspaceBoundary(tmp_path)
    boundary.ensure_termicode_dir()
    registry = build_test_registry()

    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                content="Plan: Step 1 is to create file a.txt with content 'Hello'.",
                tool_calls=[
                    ToolCall(
                        id="call_write_1",
                        name="write_file",
                        arguments={"path": "a.txt", "content": "Hello"},
                        rationale="Write 'Hello' into a.txt.",
                    )
                ],
                finish_reason="tool_calls",
            ),
            CompletionResponse(
                content="Plan: Step 2 is to read a.txt back to display its contents to the user.",
                tool_calls=[
                    ToolCall(
                        id="call_read_1",
                        name="read_file",
                        arguments={"path": "a.txt"},
                        rationale="Read back a.txt to verify its content.",
                    )
                ],
                finish_reason="tool_calls",
            ),
            CompletionResponse(
                content=(
                    "Successfully created file a.txt with content 'Hello' "
                    "and confirmed contents: Hello"
                ),
                finish_reason="stop",
            ),
        ]
    )

    approval_handler = MagicMock(return_value=True)

    runtime = AgentRuntime(
        provider=fake_provider,
        registry=registry,
        approval_handler=approval_handler,
    )

    req = AgentRequest(
        task="Create file a.txt with content 'Hello' and show it.",
        workspace=boundary,
        workflow=WorkflowType.GENERATE,
        max_steps=10,
    )

    result = runtime.run(req)

    assert result.stop_reason == StopReason.COMPLETED
    assert result.steps_used == 2
    assert (tmp_path / "a.txt").is_file()
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "Hello"
    assert "Successfully created file a.txt" in result.summary

    # write_file requires user confirmation (risk WRITE)
    # read_file is read-only (risk READ) and should not trigger approval handler
    assert approval_handler.call_count == 1
    call_action = approval_handler.call_args[0][0]
    assert isinstance(call_action, ProposedAction)
    assert call_action.tool_name == "write_file"

    assert result.run_summary is not None
    assert len(result.run_summary.approved_actions) == 1
    assert result.run_summary.approved_actions[0].tool_name == "write_file"
    assert "a.txt" in result.run_summary.changed_files


# ==============================================================================
# 2. RAG Scenario: Retrieve text in page 2 of document.pdf
# ==============================================================================


def test_scenario_rag_retrieve_document_page(tmp_path: Path) -> None:
    """Test RAG retrieval scenario extracting specific page text from document."""
    boundary = WorkspaceBoundary(tmp_path)
    boundary.ensure_termicode_dir()
    registry = build_test_registry()

    # Create mock document with clear page markers
    doc_file = tmp_path / "document.pdf"
    doc_file.write_text(
        "Page 1\n"
        "Introduction to TermiCode system.\n\n"
        "--- Page 2 ---\n"
        "DeepMind systems architecture overview and MCP integration details.\n\n"
        "Page 3\n"
        "Concluding remarks and performance benchmarks.\n",
        encoding="utf-8",
    )

    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                content="Plan: Retrieve page 2 of document.pdf using RAG retriever.",
                tool_calls=[
                    ToolCall(
                        id="call_rag_1",
                        name="rag_retrieve",
                        arguments={
                            "document_path": "document.pdf",
                            "page": 2,
                            "query": "architecture",
                        },
                        rationale="Extract architecture notes from page 2.",
                    )
                ],
                finish_reason="tool_calls",
            ),
            CompletionResponse(
                content=(
                    "Page 2 of document.pdf contains: DeepMind systems architecture overview "
                    "and MCP integration details."
                ),
                finish_reason="stop",
            ),
        ]
    )

    runtime = AgentRuntime(provider=fake_provider, registry=registry)

    req = AgentRequest(
        task="What is the text in page 2 of document.pdf?",
        workspace=boundary,
        workflow=WorkflowType.UNDERSTAND,
        max_steps=10,
    )

    result = runtime.run(req)

    assert result.stop_reason == StopReason.COMPLETED
    assert result.steps_used == 1
    assert "DeepMind systems architecture overview" in result.summary

    # Verify RAG tool executed directly and did not require approval (read risk)
    assert result.transcript_path is not None
    transcript_text = result.transcript_path.read_text(encoding="utf-8")
    assert "rag_retrieve" in transcript_text
    assert "architecture" in transcript_text


# ==============================================================================
# 3. MCP Scenario: Query GitHub for issues in repo X
# ==============================================================================


def test_scenario_mcp_query_github_issues(tmp_path: Path) -> None:
    """Test remote MCP tool execution routed via ToolBridge client."""
    boundary = WorkspaceBoundary(tmp_path)
    boundary.ensure_termicode_dir()
    registry = build_test_registry()

    client = ToolBridgeClient()
    spec = {
        "namespaced_name": "github__list_issues",
        "original_name": "list_issues",
        "description": "List issues from a GitHub repository",
        "sensitivity_tier": "SAFE",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "state": {"type": "string"},
            },
            "required": ["repo"],
        },
    }
    mcp_tool = MCPTool("github_server", spec, client)
    registry.register(mcp_tool)

    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                content="Plan: Query ToolBridge GitHub service for issues in repo X.",
                tool_calls=[
                    ToolCall(
                        id="call_mcp_1",
                        name="github__list_issues",
                        arguments={"repo": "X", "state": "open"},
                        rationale="Fetch open issues from repository X.",
                    )
                ],
                finish_reason="tool_calls",
            ),
            CompletionResponse(
                content="Found 1 open issue in repo X: #101 'MCP gateway parser error'.",
                finish_reason="stop",
            ),
        ]
    )

    approval_handler = MagicMock(return_value=True)

    runtime = AgentRuntime(
        provider=fake_provider,
        registry=registry,
        approval_handler=approval_handler,
    )

    mock_gateway_response = {
        "issues": [{"number": 101, "title": "MCP gateway parser error", "state": "open"}]
    }

    req = AgentRequest(
        task="Query GitHub for issues in repo X.",
        workspace=boundary,
        workflow=WorkflowType.GENERATE,
        max_steps=10,
    )

    with patch.object(client, "invoke_tool", return_value=mock_gateway_response) as mock_invoke:
        result = runtime.run(req)

        assert result.stop_reason == StopReason.COMPLETED
        assert result.steps_used == 1
        assert "MCP gateway parser error" in result.summary

        # Remote MCP tools require approval
        assert approval_handler.call_count == 1
        mock_invoke.assert_called_once_with(
            "github__list_issues",
            {"repo": "X", "state": "open"},
        )


# ==============================================================================
# 4. Step Termination: Stops after reasonable steps without exhausting budget
# ==============================================================================


def test_agent_loop_stops_after_reasonable_steps(tmp_path: Path) -> None:
    """Verify that agent finishes when model completes, without running up to max_steps."""
    boundary = WorkspaceBoundary(tmp_path)
    boundary.ensure_termicode_dir()
    registry = build_test_registry()

    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                content="Plan: List directory files.",
                tool_calls=[
                    ToolCall(
                        id="call_list_1",
                        name="list_files",
                        arguments={"path": "."},
                    )
                ],
                finish_reason="tool_calls",
            ),
            CompletionResponse(
                content="The workspace is empty except for .termicode metadata.",
                finish_reason="stop",
            ),
        ]
    )

    runtime = AgentRuntime(provider=fake_provider, registry=registry)

    req = AgentRequest(
        task="Inspect the workspace files.",
        workspace=boundary,
        workflow=WorkflowType.UNDERSTAND,
        max_steps=10,
    )

    result = runtime.run(req)

    assert result.stop_reason == StopReason.COMPLETED
    assert result.steps_used == 1
    assert result.steps_used < 10
    assert len(fake_provider.calls) == 2


# ==============================================================================
# 5. Repetition Guard: Breaks loop on stuck agent repeating identical actions
# ==============================================================================


def test_loop_repetition_guard_breaks_on_stuck_agent(tmp_path: Path) -> None:
    """Verify repetition guard detects loop and halts with StopReason.ERROR."""
    boundary = WorkspaceBoundary(tmp_path)
    boundary.ensure_termicode_dir()
    registry = build_test_registry()

    # Provider endlessly proposing the exact same tool call and arguments
    stuck_responses = [
        CompletionResponse(
            content=f"Attempt {i}: Reading missing.txt",
            tool_calls=[
                ToolCall(
                    id=f"call_stuck_{i}",
                    name="read_file",
                    arguments={"path": "missing.txt"},
                )
            ],
            finish_reason="tool_calls",
        )
        for i in range(10)
    ]

    fake_provider = FakeProvider(responses=stuck_responses)
    runtime = AgentRuntime(provider=fake_provider, registry=registry)

    req = AgentRequest(
        task="Read missing file repeatedly.",
        workspace=boundary,
        workflow=WorkflowType.UNDERSTAND,
        max_steps=10,
        repetition_guard=True,
        repetition_threshold=3,
    )

    result = runtime.run(req)

    assert result.stop_reason == StopReason.ERROR
    assert result.steps_used == 3
    assert "Repetition loop detected" in result.summary
    assert result.run_summary is not None
    assert any("Loop guard" in issue for issue in result.run_summary.unresolved_issues)

    # Check transcript logs repetition event
    assert result.transcript_path is not None
    transcript_text = result.transcript_path.read_text(encoding="utf-8")
    assert "repetition_detected" in transcript_text


# ==============================================================================
# 6. Error & Recovery: Graceful handling of API, auth, and remote errors
# ==============================================================================


def test_api_error_handling_graceful_recovery_and_abort(tmp_path: Path) -> None:
    """Test error handling across validation repair, fatal auth error, and MCP failure."""
    boundary = WorkspaceBoundary(tmp_path)
    boundary.ensure_termicode_dir()
    registry = build_test_registry()

    # Sub-case A: Validation repair recovery
    (tmp_path / "hello.txt").write_text("World", encoding="utf-8")
    provider_repair = FakeProvider(
        responses=[
            # Step 1: Missing required argument 'path'
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="call_bad_1",
                        name="read_file",
                        arguments={},
                    )
                ],
                finish_reason="tool_calls",
            ),
            # Step 2: Repaired argument
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="call_good_2",
                        name="read_file",
                        arguments={"path": "hello.txt"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            # Step 3: Final answer
            CompletionResponse(
                content="File hello.txt contains 'World'.",
                finish_reason="stop",
            ),
        ]
    )

    runtime_repair = AgentRuntime(provider=provider_repair, registry=registry)
    req_repair = AgentRequest(
        task="Read hello.txt",
        workspace=boundary,
        workflow=WorkflowType.UNDERSTAND,
        max_repairs=3,
    )
    result_repair = runtime_repair.run(req_repair)
    assert result_repair.stop_reason == StopReason.COMPLETED
    # Consecutive repairs counter resets to 0 upon successful repair execution
    assert result_repair.repairs_used == 0
    assert result_repair.transcript_path is not None
    assert "repair_needed" in result_repair.transcript_path.read_text(encoding="utf-8")
    assert "World" in result_repair.summary

    # Sub-case B: Fatal ProviderAuthError
    provider_auth_err = FakeProvider(
        responses=[ProviderAuthError("401 Unauthorized: Invalid API key")]
    )
    runtime_auth = AgentRuntime(provider=provider_auth_err, registry=registry)
    req_auth = AgentRequest(
        task="Read files",
        workspace=boundary,
        workflow=WorkflowType.UNDERSTAND,
    )
    result_auth = runtime_auth.run(req_auth)
    assert result_auth.stop_reason == StopReason.ERROR
    assert result_auth.run_summary is not None
    assert any(
        "Fatal provider error" in issue for issue in result_auth.run_summary.unresolved_issues
    )

    # Sub-case C: ToolBridge MCP connection failure fed back as observation
    client = ToolBridgeClient()
    spec = {
        "namespaced_name": "gateway__ping",
        "original_name": "ping",
        "description": "Gateway ping",
        "sensitivity_tier": "SAFE",
        "input_schema": {"type": "object", "properties": {}},
    }
    mcp_tool = MCPTool("test_gw", spec, client)
    registry_mcp = build_test_registry()
    registry_mcp.register(mcp_tool)

    provider_mcp = FakeProvider(
        responses=[
            CompletionResponse(
                content="Plan: Ping ToolBridge gateway.",
                tool_calls=[
                    ToolCall(
                        id="call_ping_1",
                        name="gateway__ping",
                        arguments={},
                    )
                ],
                finish_reason="tool_calls",
            ),
            CompletionResponse(
                content="ToolBridge gateway is currently unreachable. Halting gracefully.",
                finish_reason="stop",
            ),
        ]
    )

    runtime_mcp = AgentRuntime(
        provider=provider_mcp,
        registry=registry_mcp,
        approval_handler=lambda a, d: True,
    )
    req_mcp = AgentRequest(
        task="Ping gateway",
        workspace=boundary,
        workflow=WorkflowType.GENERATE,
    )

    with patch.object(
        client, "invoke_tool", side_effect=ToolBridgeConnectionError("Connection refused")
    ):
        result_mcp = runtime_mcp.run(req_mcp)
        assert result_mcp.stop_reason == StopReason.COMPLETED
        assert "unreachable" in result_mcp.summary


# ==============================================================================
# 7. Debug Mode: Prints planner, tool selection, execution, and observation
# ==============================================================================


def test_debug_mode_prints_planner_and_tool_steps(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify debug mode logs planner thought, tool selection, execution, and observation."""
    boundary = WorkspaceBoundary(tmp_path)
    boundary.ensure_termicode_dir()
    registry = build_test_registry()

    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                content="Reasoning: I will list files to understand workspace structure.",
                tool_calls=[
                    ToolCall(
                        id="call_list_1",
                        name="list_files",
                        arguments={"path": "."},
                    )
                ],
                finish_reason="tool_calls",
            ),
            CompletionResponse(
                content="Reasoning: File listing complete. Finalizing answer.",
                finish_reason="stop",
            ),
        ]
    )

    runtime = AgentRuntime(provider=fake_provider, registry=registry, debug=True)

    req = AgentRequest(
        task="Inspect directory layout.",
        workspace=boundary,
        workflow=WorkflowType.UNDERSTAND,
        debug=True,
    )

    result = runtime.run(req)

    assert result.stop_reason == StopReason.COMPLETED

    captured = capsys.readouterr()
    all_output = captured.out + captured.err

    assert "[Planner]" in all_output
    assert "[Tool Selection]" in all_output
    assert "[Execution]" in all_output
    assert "[Observation]" in all_output
    assert "[Final Answer]" in all_output


# ==============================================================================
# 8. CLI Multi-step Sample Task Execution
# ==============================================================================


def test_cli_run_multistep_sample_task(tmp_path: Path) -> None:
    """Verify CLI `termicode run` completes multi-step task offline with debug logging."""
    result = runner.invoke(
        app,
        [
            "--workspace",
            str(tmp_path),
            "run",
            "Create file a.txt with content 'Hello' and show it",
            "--offline",
            "-y",
            "--debug",
        ],
    )

    assert result.exit_code == 0
    assert "Execution Summary" in result.output
    assert "completed" in result.output

    # Verify created file on disk
    target_file = tmp_path / "a.txt"
    assert target_file.is_file()
    assert target_file.read_text(encoding="utf-8").strip() == "Hello"

    # Verify debug output present in output
    assert (
        "[DEBUG]" in result.output or "[Planner]" in result.output or "write_file" in result.output
    )
