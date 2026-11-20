"""Comprehensive End-to-End Integration Test Suite for TermiCode AI + ToolBridge.

Covers:
1. Full Chat with RAG workflow (document attachment, intent analysis, context injection).
2. Agent-run code task end-to-end (code generation, test creation, command execution, completion).
3. ToolBridge integration end-to-end (server registration, tool discovery, MCP tool execution).
4. Session persistence across restarts (SQLite storage, message & metadata reload).
5. Robust failure handling (missing credentials, unreachable gateway, disabled tools).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch
from urllib.error import URLError

import pytest

from termicode.agent.models import AgentRequest, StopReason
from termicode.agent.runtime import AgentRuntime
from termicode.agent.workflows import WorkflowType
from termicode.chat.database import SessionDatabase
from termicode.chat.engine import ChatEngine
from termicode.chat.session import ChatSession
from termicode.config import Settings
from termicode.providers.base import (
    CompletionRequest,
    CompletionResponse,
    MessageRole,
    ProviderAuthError,
    ToolCall,
)
from termicode.providers.fake import FakeProvider
from termicode.tools.base import ToolContext
from termicode.tools.command import RunCommandTool
from termicode.tools.filesystem import ReadFileTool, WriteFileTool
from termicode.tools.registry import ToolRegistry
from termicode.tools.toolbridge import (
    MCPTool,
    ToolBridgeClient,
    ToolBridgeConfig,
    ToolBridgeConnectionError,
    ToolBridgeServerManager,
)
from termicode.workspace import WorkspaceBoundary


class MockHTTPResponse:
    """Mock context manager for urllib urlopen responses."""

    def __init__(self, data: dict[str, Any] | list[Any], status: int = 200) -> None:
        self.data = data
        self.status = status
        self.raw_bytes = json.dumps(data).encode("utf-8")

    def read(self) -> bytes:
        return self.raw_bytes

    def __enter__(self) -> MockHTTPResponse:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass


# ==============================================================================
# 1. Full Chat with RAG Workflow
# ==============================================================================


def test_chat_rag_workflow_end_to_end(tmp_path: Path) -> None:
    """Verify document attachment, context indexing, and query generation with RAG context."""
    boundary = WorkspaceBoundary(tmp_path)
    boundary.ensure_termicode_dir()
    settings = Settings.load(workspace=tmp_path)
    settings.provider = "fake"

    # Create workspace knowledge document
    doc_path = tmp_path / "architecture.md"
    doc_path.write_text(
        "# System Architecture\nTermiCode AI uses a 7-layer safety boundary.\n"
        "All filesystem writes are confined to the workspace root.",
        encoding="utf-8",
    )

    session = ChatSession(workspace=boundary, settings=settings)
    attached = session.add_document("architecture.md")

    assert attached.filename == "architecture.md"
    assert attached.size_bytes > 0
    assert len(session.list_documents()) == 1

    captured_prompt = ""

    def mock_complete(req: CompletionRequest) -> CompletionResponse:
        nonlocal captured_prompt
        # Inspect system prompt to verify document attachment context was injected
        for msg in req.messages:
            if msg.role == MessageRole.SYSTEM:
                captured_prompt = msg.content
        return CompletionResponse(
            content="Based on architecture.md, TermiCode uses a 7-layer safety boundary.",
            finish_reason="stop",
        )

    fake_provider = FakeProvider()
    fake_provider.complete = MagicMock(side_effect=mock_complete)

    engine = ChatEngine(session=session, boundary=boundary, provider=fake_provider)
    reply, steps = engine.generate_response("What safety architecture does TermiCode use?")

    assert "7-layer safety boundary" in reply
    assert "architecture.md" in captured_prompt
    assert any(s["phase"] == "Context" for s in steps)
    assert any("architecture.md" in s["detail"] for s in steps)

    # Verify history recorded in session
    history = session.get_history()
    assert len(history) == 2
    assert history[0].role == "user"
    assert history[1].role == "assistant"
    assert "7-layer safety boundary" in history[1].content


# ==============================================================================
# 2. Agent-Run Code Task End-to-End
# ==============================================================================


def test_agent_run_code_task_generation_and_execution(tmp_path: Path) -> None:
    """Verify agent creates source code, writes test file, runs pytest, and finishes."""
    boundary = WorkspaceBoundary(tmp_path)
    boundary.ensure_termicode_dir()

    registry = ToolRegistry()
    registry.register(WriteFileTool())
    registry.register(ReadFileTool())
    registry.register(RunCommandTool())

    math_code = "def multiply(a: int, b: int) -> int:\n    return a * b\n"
    test_code = (
        "from math_lib import multiply\n\n"
        "def test_multiply():\n"
        "    assert multiply(3, 4) == 12\n"
    )

    fake_provider = FakeProvider(
        responses=[
            # Step 1: Write math_lib.py
            CompletionResponse(
                content="Step 1: Write implementation in math_lib.py",
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        name="write_file",
                        arguments={"path": "math_lib.py", "content": math_code},
                    )
                ],
                finish_reason="tool_calls",
            ),
            # Step 2: Write test_math.py
            CompletionResponse(
                content="Step 2: Write test file in test_math.py",
                tool_calls=[
                    ToolCall(
                        id="call_2",
                        name="write_file",
                        arguments={"path": "test_math.py", "content": test_code},
                    )
                ],
                finish_reason="tool_calls",
            ),
            # Step 3: Run pytest
            CompletionResponse(
                content="Step 3: Run unit tests via runner",
                tool_calls=[
                    ToolCall(
                        id="call_3",
                        name="run_command",
                        arguments={"command": ["python", "-m", "pytest", "test_math.py"]},
                    )
                ],
                finish_reason="tool_calls",
            ),
            # Final Completion
            CompletionResponse(
                content="All tests created and verified successfully.",
                finish_reason="stop",
            ),
        ]
    )

    # Approve all planned actions
    approval_handler = MagicMock(return_value=True)

    runtime = AgentRuntime(
        provider=fake_provider,
        registry=registry,
        approval_handler=approval_handler,
    )

    # Mock command runner so tests are deterministic and fast
    with patch("termicode.runner.runner.CommandRunner.run") as mock_run:
        from termicode.runner import CommandResult

        mock_run.return_value = CommandResult(
            exit_code=0,
            stdout="collected 1 item\ntest_math.py . [100%]\n1 passed in 0.05s",
            stderr="",
            duration_seconds=0.05,
            timed_out=False,
            command=["python", "-m", "pytest", "test_math.py"],
            cwd=str(tmp_path),
        )

        req = AgentRequest(
            task="Create multiply function and verify with pytest",
            workspace=boundary,
            workflow=WorkflowType.GENERATE,
            max_steps=5,
        )

        result = runtime.run(req)

    assert result.stop_reason == StopReason.COMPLETED
    assert result.steps_used == 3
    assert (tmp_path / "math_lib.py").exists()
    assert (tmp_path / "test_math.py").exists()
    assert "def multiply" in (tmp_path / "math_lib.py").read_text(encoding="utf-8")


# ==============================================================================
# 3. ToolBridge Gateway Calls End-to-End
# ==============================================================================


def test_toolbridge_connect_discover_and_invoke_e2e(tmp_path: Path) -> None:
    """Verify ToolBridge server connection, tool discovery, registration, and tool invocation."""
    boundary = WorkspaceBoundary(tmp_path)
    boundary.ensure_termicode_dir()

    server_mgr = ToolBridgeServerManager(workspace_root=tmp_path)
    server_mgr.add_server(
        name="production_gateway",
        url="http://127.0.0.1:8000",
        token="tb_admin_token_sec123",
    )

    servers = server_mgr.list_servers()
    assert any(s["name"] == "production_gateway" for s in servers)
    has_token = any(
        s["name"] == "production_gateway" and s["token"] == "tb_admin_token_sec123"
        for s in servers
    )
    assert has_token

    mock_tools_payload = {
        "status": "success",
        "count": 1,
        "tools": [
            {
                "name": "remote_echo",
                "server_id": "production_gateway",
                "description": "Echo back test payload",
                "input_schema": {
                    "type": "object",
                    "properties": {"message": {"type": "string"}},
                    "required": ["message"],
                },
            }
        ],
    }

    mock_invoke_payload = {
        "status": "success",
        "result": {"echo": "hello from toolbridge gateway"},
        "audit_id": "audit_8899",
    }

    def fake_urlopen(req: Any, **kwargs: Any) -> MockHTTPResponse:
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "/api/v1/tools" in url and not url.endswith("/invoke"):
            return MockHTTPResponse(mock_tools_payload)
        elif "/invoke" in url:
            return MockHTTPResponse(mock_invoke_payload)
        return MockHTTPResponse({"status": "ok"})

    with patch("termicode.tools.toolbridge.urlopen", side_effect=fake_urlopen):
        cfg = ToolBridgeConfig(api_url="http://127.0.0.1:8000", api_key="tb_admin_token_sec123")
        client = ToolBridgeClient(config=cfg)
        discovered = client.list_tools()
        assert len(discovered) == 1
        assert discovered[0]["name"] == "remote_echo"

        # Register into TermiCode registry as MCPTool
        registry = ToolRegistry()
        mcp_tool = MCPTool(
            server_name="production_gateway",
            tool_spec=discovered[0],
            client=client,
        )
        registry.register(mcp_tool)

        assert registry.has("remote_echo")
        assert registry.get("remote_echo").is_mcp is True

        ctx = ToolContext(workspace=boundary, user_confirmed=True)
        tool_res = registry.execute("remote_echo", {"message": "ping"}, ctx)

        assert tool_res.ok
        assert "hello from toolbridge gateway" in str(tool_res.output)


# ==============================================================================
# 4. Session Persistence Across Process Restarts
# ==============================================================================


def test_session_persistence_across_restarts(tmp_path: Path) -> None:
    """Verify session data and conversation logs survive session rebuilds / restarts."""
    boundary = WorkspaceBoundary(tmp_path)
    boundary.ensure_termicode_dir()
    settings = Settings.load(workspace=tmp_path)
    settings.provider = "fake"

    # Phase A: Initial Session
    session_1 = ChatSession(workspace=boundary, settings=settings)
    sid = session_1.state.session_id

    session_1.add_user_message("Hello, remember my secret project code: ALPHA_77")
    session_1.add_assistant_message(
        "Acknowledged. I will track ALPHA_77.",
        step_logs=[{"phase": "Memory", "action": "Store", "detail": "Recorded code ALPHA_77"}],
    )
    assert len(session_1.get_history()) == 2

    # Phase B: Simulated Restart - New process initializes new ChatSession instance
    session_2 = ChatSession(workspace=boundary, settings=settings, session_id=sid)
    assert session_2.state.session_id == sid

    restored_history = session_2.get_history()
    assert len(restored_history) == 2
    assert restored_history[0].role == "user"
    assert "ALPHA_77" in restored_history[0].content
    assert restored_history[1].role == "assistant"
    assert "ALPHA_77" in restored_history[1].content
    assert len(restored_history[1].step_logs) == 1
    assert restored_history[1].step_logs[0]["action"] == "Store"

    # Verify switching and listing sessions persists
    db = SessionDatabase(tmp_path / ".termicode" / "chat_sessions.db")
    sessions = db.list_sessions()
    assert any(s["id"] == sid for s in sessions)


# ==============================================================================
# 5. Robust Failure Handling & Error Boundaries
# ==============================================================================


def test_failure_handling_provider_auth_error(tmp_path: Path) -> None:
    """Verify graceful handling when LLM provider has invalid or missing credentials."""
    boundary = WorkspaceBoundary(tmp_path)
    boundary.ensure_termicode_dir()
    settings = Settings.load(workspace=tmp_path)

    session = ChatSession(workspace=boundary, settings=settings)

    class AuthFailingProvider(FakeProvider):
        def complete(self, request: CompletionRequest) -> CompletionResponse:
            raise ProviderAuthError("OpenAI API key missing or expired.")

    failing_provider = AuthFailingProvider()
    engine = ChatEngine(session=session, boundary=boundary, provider=failing_provider)

    reply, steps = engine.generate_response("Analyze current repository")

    # Assert user-friendly guidance provided without crash
    assert "Provider Authentication Notice" in reply
    assert "OPENAI_API_KEY" in reply
    assert not reply.startswith("Traceback")


def test_failure_handling_unreachable_toolbridge_gateway(tmp_path: Path) -> None:
    """Verify graceful exception and friendly reporting when ToolBridge gateway is offline."""
    cfg = ToolBridgeConfig(api_url="http://127.0.0.1:9999", api_key="invalid")
    client = ToolBridgeClient(config=cfg)

    def failing_urlopen(*args: Any, **kwargs: Any) -> Any:
        raise URLError("Connection refused [Errno 111]")

    with patch("termicode.tools.toolbridge.urlopen", side_effect=failing_urlopen):
        with pytest.raises(ToolBridgeConnectionError) as exc_info:
            client.list_tools()

        assert "Failed to reach ToolBridge" in str(exc_info.value)
