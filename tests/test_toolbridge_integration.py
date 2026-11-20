"""Comprehensive tests for TermiCode AI integration with ToolBridge MCP Gateway."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest
from pydantic import BaseModel, ValidationError
from rich.console import Console
from typer.testing import CliRunner

from termicode.agent.models import AgentRequest, ProposedAction
from termicode.agent.policy import PolicyEngine
from termicode.agent.runtime import AgentRuntime
from termicode.agent.workflows import get_workflow_profile
from termicode.chat import ChatEngine, ChatREPL, ChatSession
from termicode.cli import app
from termicode.config import Settings
from termicode.providers.base import CompletionResponse, ToolCall
from termicode.providers.fake import FakeProvider
from termicode.tools.base import ToolContext
from termicode.tools.registry import ToolRegistry
from termicode.tools.toolbridge import (
    MCPTool,
    ToolBridgeAuthError,
    ToolBridgeClient,
    ToolBridgeConfig,
    ToolBridgeConnectionError,
    ToolBridgeExecutionError,
    ToolBridgeRateLimitError,
    ToolBridgeServerManager,
    create_pydantic_schema_from_json,
)
from termicode.workspace import WorkspaceBoundary

runner = CliRunner()


class MockHTTPResponse:
    """Mock context manager for urllib urlopen."""

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
# 1. ToolBridgeClient Tests
# ==============================================================================


def test_client_request_construction() -> None:
    """Verify headers, auth token, and serialization in urllib request."""
    cfg = ToolBridgeConfig(api_url="http://localhost:8000/", api_key="secret-token-123")
    client = ToolBridgeClient(cfg)
    req = client._build_request("/api/v1/tools", method="POST", data={"key": "val"})

    assert req.full_url == "http://localhost:8000/api/v1/tools"
    assert req.get_method() == "POST"
    assert req.headers["Accept"] == "application/json"
    assert req.headers["Content-type"] == "application/json"
    assert req.headers["X-api-key"] == "secret-token-123"


def test_client_check_health_success() -> None:
    """Verify health endpoint successfully parses gateway payload."""
    client = ToolBridgeClient()
    mock_resp = MockHTTPResponse({"status": "healthy", "ok": True, "uptime": 120.5})

    with patch("termicode.tools.toolbridge.urlopen", return_value=mock_resp):
        res = client.check_health()
        assert res["status"] == "healthy"
        assert res["ok"] is True


def test_client_check_health_connection_error() -> None:
    """Verify URLError triggers ToolBridgeConnectionError."""
    client = ToolBridgeClient()
    with patch(
        "termicode.tools.toolbridge.urlopen",
        side_effect=URLError("Connection refused"),
    ):
        with pytest.raises(ToolBridgeConnectionError) as exc_info:
            client.check_health()
        assert "Failed to connect to ToolBridge" in str(exc_info.value)


def test_client_list_tools_success() -> None:
    """Verify querying list of tools formats dictionary response."""
    client = ToolBridgeClient()
    mock_payload = {
        "tools": [
            {
                "namespaced_name": "filesystem__read_file",
                "original_name": "read_file",
                "description": "Read file contents",
                "sensitivity_tier": "SAFE",
                "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
            }
        ]
    }
    with patch("termicode.tools.toolbridge.urlopen", return_value=MockHTTPResponse(mock_payload)):
        tools = client.list_tools()
        assert len(tools) == 1
        assert tools[0]["namespaced_name"] == "filesystem__read_file"


def test_client_list_tools_auth_and_rate_limit_errors() -> None:
    """Verify HTTP 401 and 429 raise appropriate typed exceptions."""
    client = ToolBridgeClient()

    err_401 = HTTPError("http://test", 401, "Unauthorized", {}, io.BytesIO(b"Forbidden"))  # type: ignore[arg-type]
    with patch("termicode.tools.toolbridge.urlopen", side_effect=err_401):
        with pytest.raises(ToolBridgeAuthError):
            client.list_tools()

    err_429 = HTTPError("http://test", 429, "Too Many Requests", {}, io.BytesIO(b"Rate limited"))  # type: ignore[arg-type]
    with patch("termicode.tools.toolbridge.urlopen", side_effect=err_429):
        with pytest.raises(ToolBridgeRateLimitError):
            client.list_tools()


def test_client_invoke_tool_success() -> None:
    """Verify tool invocation sends payload and returns parsed result."""
    client = ToolBridgeClient()
    mock_payload = {
        "result": "Hello ToolBridge",
        "tool_name": "filesystem__echo",
        "duration_ms": 1.25,
    }
    with patch("termicode.tools.toolbridge.urlopen", return_value=MockHTTPResponse(mock_payload)):
        res = client.invoke_tool("filesystem__echo", {"message": "Hello"})
        assert res["result"] == "Hello ToolBridge"
        assert res["tool_name"] == "filesystem__echo"


def test_client_invoke_tool_execution_error() -> None:
    """Verify HTTP 500 error raises ToolBridgeExecutionError."""
    client = ToolBridgeClient()
    err_500 = HTTPError(
        "http://test",
        500,
        "Internal Error",
        {},
        io.BytesIO(b"Upstream failed"),  # type: ignore[arg-type]
    )
    with patch("termicode.tools.toolbridge.urlopen", side_effect=err_500):
        with pytest.raises(ToolBridgeExecutionError) as exc_info:
            client.invoke_tool("filesystem__echo", {})
        assert "ToolBridge invocation of 'filesystem__echo' failed (HTTP 500)" in str(
            exc_info.value
        )


# ==============================================================================
# 2. ToolBridgeServerManager Tests
# ==============================================================================


def test_manager_crud_and_persistence(tmp_path: Path) -> None:
    """Verify server addition, retrieval, listing, removal, and JSON persistence."""
    mgr = ToolBridgeServerManager(tmp_path)
    # Starts with default
    servers = mgr.list_servers()
    assert len(servers) >= 1
    assert any(s["name"] == "default" for s in servers)

    # Add custom server
    mgr.add_server("cluster_gateway", "https://tb.corp.internal:8443", token="corp-token")
    custom = mgr.get_server("cluster_gateway")
    assert custom is not None
    assert custom["url"] == "https://tb.corp.internal:8443"
    assert custom["token"] == "corp-token"

    # Reload new manager from disk to verify persistence
    mgr2 = ToolBridgeServerManager(tmp_path)
    assert mgr2.get_server("cluster_gateway") is not None

    # Remove server
    assert mgr2.remove_server("cluster_gateway") is True
    assert mgr2.get_server("cluster_gateway") is None
    assert mgr2.remove_server("nonexistent") is False


def test_manager_add_server_validation(tmp_path: Path) -> None:
    """Verify URL scheme validation on server addition."""
    mgr = ToolBridgeServerManager(tmp_path)
    with pytest.raises(ValueError, match="Invalid URL"):
        mgr.add_server("bad", "ftp://toolbridge.local")
    with pytest.raises(ValueError, match="Server name cannot be empty"):
        mgr.add_server("   ", "http://localhost:8000")


def test_manager_discover_and_inspect_tools(tmp_path: Path) -> None:
    """Verify discovering tools across servers and inspecting schema."""
    mgr = ToolBridgeServerManager(tmp_path)
    mgr.add_server("local_dev", "http://127.0.0.1:8000")

    mock_tools = [
        {
            "namespaced_name": "filesystem__echo",
            "original_name": "echo",
            "description": "Echo back test message",
            "sensitivity_tier": "SAFE",
            "input_schema": {
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
        }
    ]

    with patch.object(ToolBridgeClient, "list_tools", return_value=mock_tools):
        discovered = mgr.discover_tools()
        assert len(discovered) >= 1
        tool = mgr.inspect_tool("filesystem__echo")
        assert tool is not None
        assert tool["namespaced_name"] == "filesystem__echo"
        assert "message" in tool["input_schema"]["properties"]

        # Original name match
        tool_by_orig = mgr.inspect_tool("echo")
        assert tool_by_orig is not None


# ==============================================================================
# 3. Dynamic Pydantic Schema Generation Tests
# ==============================================================================


def test_dynamic_pydantic_schema_validation() -> None:
    """Verify create_pydantic_schema_from_json builds typed Pydantic models."""
    json_schema = {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "Message to print"},
            "count": {"type": "integer", "default": 1},
            "flag": {"type": "boolean"},
        },
        "required": ["message"],
    }

    schema_cls = create_pydantic_schema_from_json("EchoTestModel", json_schema)
    assert issubclass(schema_cls, BaseModel)

    # Valid instantiation
    obj = schema_cls.model_validate({"message": "Hello", "count": 3, "flag": True})
    assert obj.message == "Hello"
    assert obj.count == 3
    assert obj.flag is True

    # Missing required field raises ValidationError
    with pytest.raises(ValidationError):
        schema_cls.model_validate({"count": 3})

    # Invalid type raises ValidationError
    with pytest.raises(ValidationError):
        schema_cls.model_validate({"message": 12345, "count": "not_an_int"})


# ==============================================================================
# 4. MCPTool BaseTool Wrapper Tests
# ==============================================================================


def test_mcp_tool_properties_and_approval_gate(tmp_path: Path) -> None:
    """Verify MCPTool requires explicit confirmation before executing."""
    boundary = WorkspaceBoundary(tmp_path)
    client = ToolBridgeClient()
    spec = {
        "namespaced_name": "filesystem__ping",
        "original_name": "ping",
        "description": "Gateway ping check",
        "sensitivity_tier": "SAFE",
        "input_schema": {"type": "object", "properties": {}},
    }

    tool = MCPTool("default", spec, client)
    assert tool.is_mcp is True
    assert tool.name == "filesystem__ping"

    # Execution without user confirmation fails approval gate
    ctx_unconfirmed = ToolContext(workspace=boundary, user_confirmed=False)
    unconfirmed_result = tool.run({}, context=ctx_unconfirmed)
    assert unconfirmed_result.ok is False
    assert "User confirmation required" in (unconfirmed_result.error or "")
    assert unconfirmed_result.metadata.get("approval_required") is True

    # Execution with confirmation succeeds
    mock_payload = {"result": "pong", "tool_name": "filesystem__ping"}
    with patch.object(client, "invoke_tool", return_value=mock_payload):
        ctx_confirmed = ToolContext(workspace=boundary, user_confirmed=True)
        confirmed_result = tool.run({}, context=ctx_confirmed)
        assert confirmed_result.ok is True
        assert confirmed_result.output == "pong"
        assert confirmed_result.metadata.get("is_mcp") is True


def test_mcp_tool_handles_unreachable_server(tmp_path: Path) -> None:
    """Verify MCPTool returns clean ToolResult error when server is unreachable."""
    boundary = WorkspaceBoundary(tmp_path)
    client = ToolBridgeClient()
    spec = {
        "namespaced_name": "filesystem__ping",
        "original_name": "ping",
        "description": "Gateway ping check",
        "sensitivity_tier": "SAFE",
        "input_schema": {"type": "object", "properties": {}},
    }

    tool = MCPTool("default", spec, client)
    ctx_confirmed = ToolContext(workspace=boundary, user_confirmed=True)

    with patch.object(
        client,
        "invoke_tool",
        side_effect=ToolBridgeConnectionError("Host is offline"),
    ):
        res = tool.run({}, context=ctx_confirmed)
        assert res.ok is False
        assert "ToolBridge invocation error on 'filesystem__ping'" in (res.error or "")
        assert "Host is offline" in (res.error or "")


# ==============================================================================
# 5. Policy Engine & Agent Runtime Integration Tests
# ==============================================================================


def test_policy_engine_evaluates_mcp_tool_needs_approval(tmp_path: Path) -> None:
    """Verify PolicyEngine flags all remote MCP tools as status='needs_approval'."""
    boundary = WorkspaceBoundary(tmp_path)
    client = ToolBridgeClient()
    spec = {
        "namespaced_name": "filesystem__echo",
        "original_name": "echo",
        "description": "Echo text",
        "sensitivity_tier": "SAFE",
        "input_schema": {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
    }
    mcp_tool = MCPTool("cluster", spec, client)

    registry = ToolRegistry()
    registry.register(mcp_tool)
    engine = PolicyEngine(registry)

    # 1. Invalid args
    action_bad = ProposedAction(kind="tool_call", tool_name="filesystem__echo", arguments={})
    profile = get_workflow_profile("refactor")
    dec_bad = engine.evaluate(action_bad, profile, boundary)
    assert dec_bad.status == "invalid_arguments"

    # 2. Valid args -> needs_approval
    action_good = ProposedAction(
        kind="tool_call",
        tool_name="filesystem__echo",
        arguments={"message": "Deploying update"},
    )
    dec_good = engine.evaluate(action_good, profile, boundary)
    assert dec_good.status == "needs_approval"
    expected_msg = "Remote MCP tool 'filesystem__echo' requires explicit user confirmation"
    assert expected_msg in dec_good.reason
    assert "cluster" in (dec_good.preview or "")
    assert "Deploying update" in (dec_good.preview or "")


def test_agent_runtime_routes_mcp_tool(tmp_path: Path) -> None:
    """Verify AgentRuntime executes remote MCP tool when approval handler confirms."""
    boundary = WorkspaceBoundary(tmp_path)
    boundary.ensure_termicode_dir()
    client = ToolBridgeClient()
    spec = {
        "namespaced_name": "filesystem__echo",
        "original_name": "echo",
        "description": "Echo text",
        "sensitivity_tier": "SAFE",
        "input_schema": {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
    }
    mcp_tool = MCPTool("default", spec, client)

    registry = ToolRegistry()
    registry.register(mcp_tool)

    # Provider proposing to call the MCP tool
    provider = FakeProvider(
        responses=[
            CompletionResponse(
                content="I will call ToolBridge echo.",
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        name="filesystem__echo",
                        arguments={"message": "Gateway ready"},
                    )
                ],
            ),
            CompletionResponse(
                content="Echo call completed successfully!",
                finish_reason="stop",
            ),
        ]
    )

    approval_handler = MagicMock(return_value=True)

    runtime = AgentRuntime(
        provider=provider,
        registry=registry,
        approval_handler=approval_handler,
    )

    req = AgentRequest(
        task="Test remote tool execution",
        workspace=boundary,
        workflow="refactor",
    )

    with patch.object(client, "invoke_tool", return_value={"result": "Gateway ready echoed"}):
        result = runtime.run(req)
        assert result.stop_reason.value == "completed"
        assert result.summary == "Echo call completed successfully!"
        assert result.steps_used == 1
        assert approval_handler.called
        assert result.run_summary is not None
        assert len(result.run_summary.approved_actions) == 1
        assert result.run_summary.approved_actions[0].tool_name == "filesystem__echo"


# ==============================================================================
# 6. CLI Command Tests (termicode toolbridge ...)
# ==============================================================================


def test_cli_toolbridge_add_and_list_servers(tmp_path: Path) -> None:
    """Test CLI commands: termicode toolbridge add-server and list-servers."""
    boundary = WorkspaceBoundary(tmp_path)
    boundary.ensure_termicode_dir()

    # Add server
    res_add = runner.invoke(
        app,
        [
            "-w",
            str(tmp_path),
            "toolbridge",
            "add-server",
            "prod_gateway",
            "http://127.0.0.1:8000",
            "--token",
            "super-secret-token",
        ],
    )
    assert res_add.exit_code == 0
    assert "Registered ToolBridge server:" in res_add.stdout
    assert "prod_gateway" in res_add.stdout

    # List servers
    with patch.object(
        ToolBridgeClient,
        "check_health",
        return_value={"status": "healthy", "ok": True},
    ):
        res_list = runner.invoke(app, ["-w", str(tmp_path), "toolbridge", "list-servers"])
        assert res_list.exit_code == 0
        assert "prod_gateway" in res_list.stdout
        assert "Healthy" in res_list.stdout


def test_cli_toolbridge_list_and_inspect_tools(tmp_path: Path) -> None:
    """Test CLI commands: termicode toolbridge list-tools and inspect-tool."""
    boundary = WorkspaceBoundary(tmp_path)
    boundary.ensure_termicode_dir()

    mock_tools = [
        {
            "namespaced_name": "filesystem__read_file",
            "original_name": "read_file",
            "description": "Read file contents",
            "sensitivity_tier": "SAFE",
            "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
        }
    ]

    with patch.object(ToolBridgeClient, "list_tools", return_value=mock_tools):
        res_list = runner.invoke(app, ["-w", str(tmp_path), "toolbridge", "list-tools"])
        assert res_list.exit_code == 0
        assert "filesystem__read_file" in res_list.stdout

        res_inspect = runner.invoke(
            app,
            ["-w", str(tmp_path), "toolbridge", "inspect-tool", "filesystem__read_file"],
        )
        assert res_inspect.exit_code == 0
        assert "Tool Specification: filesystem__read_file" in res_inspect.stdout
        assert "Input Schema" in res_inspect.stdout


def test_cli_toolbridge_call_tool_denied_and_approved(tmp_path: Path) -> None:
    """Test CLI command: termicode toolbridge call-tool approval gate."""
    boundary = WorkspaceBoundary(tmp_path)
    boundary.ensure_termicode_dir()

    # Denied by interactive prompt (input "n")
    res_deny = runner.invoke(
        app,
        [
            "-w",
            str(tmp_path),
            "toolbridge",
            "call-tool",
            "default",
            "filesystem__echo",
            "--json",
            '{"message": "hi"}',
        ],
        input="n\n",
    )
    assert res_deny.exit_code == 0
    assert "Execution cancelled by user" in res_deny.stdout

    # Approved via --yes flag
    mock_payload = {"result": "Echo response: hi", "tool_name": "filesystem__echo"}
    with patch.object(ToolBridgeClient, "invoke_tool", return_value=mock_payload):
        res_approve = runner.invoke(
            app,
            [
                "-w",
                str(tmp_path),
                "toolbridge",
                "call-tool",
                "default",
                "filesystem__echo",
                "--json",
                '{"message": "hi"}',
                "--yes",
            ],
        )
        assert res_approve.exit_code == 0
        assert "Tool Invocation Successful" in res_approve.stdout
        assert "Echo response: hi" in res_approve.stdout


# ==============================================================================
# 7. REPL /mcp Slash Commands Tests
# ==============================================================================


def test_repl_mcp_servers_and_list_command(tmp_path: Path) -> None:
    """Test ChatREPL handles /mcp servers and /mcp list commands."""
    boundary = WorkspaceBoundary(tmp_path)
    boundary.ensure_termicode_dir()
    settings = Settings.load(workspace=boundary.root)
    session = ChatSession(workspace=boundary, settings=settings)
    engine = ChatEngine(session=session, boundary=boundary)

    mock_tools = [
        {
            "namespaced_name": "filesystem__ping",
            "original_name": "ping",
            "description": "Gateway ping check",
            "sensitivity_tier": "SAFE",
            "input_schema": {},
        }
    ]

    with patch.object(
        ToolBridgeClient,
        "check_health",
        return_value={"status": "healthy", "ok": True},
    ):
        with patch.object(ToolBridgeClient, "list_tools", return_value=mock_tools):
            out_stream = io.StringIO()
            repl = ChatREPL(
                engine=engine,
                console=Console(file=out_stream, width=120),
                input_stream=io.StringIO(""),
                enable_streaming_animation=False,
            )

            repl.handle_command("/mcp servers")
            output_servers = out_stream.getvalue()
            assert "ToolBridge Servers" in output_servers

            out_stream.truncate(0)
            out_stream.seek(0)
            repl.handle_command("/mcp list")
            output_list = out_stream.getvalue()
            assert "filesystem__ping" in output_list


def test_repl_mcp_call_cancelled_and_approved(tmp_path: Path) -> None:
    """Test ChatREPL /mcp call approval confirmation prompt."""
    boundary = WorkspaceBoundary(tmp_path)
    boundary.ensure_termicode_dir()
    settings = Settings.load(workspace=boundary.root)
    session = ChatSession(workspace=boundary, settings=settings)
    engine = ChatEngine(session=session, boundary=boundary)

    # 1. Deny execution
    out_deny = io.StringIO()
    repl_deny = ChatREPL(
        engine=engine,
        console=Console(file=out_deny, width=120),
        input_stream=io.StringIO("n\n"),
        enable_streaming_animation=False,
    )
    repl_deny.handle_command('/mcp call default filesystem__echo {"message": "test"}')
    assert "Execution cancelled by user." in out_deny.getvalue()

    # 2. Approve execution
    mock_payload = {"result": "test echo result"}
    with patch.object(ToolBridgeClient, "invoke_tool", return_value=mock_payload):
        out_approve = io.StringIO()
        repl_approve = ChatREPL(
            engine=engine,
            console=Console(file=out_approve, width=120),
            input_stream=io.StringIO("y\n"),
            enable_streaming_animation=False,
        )
        repl_approve.handle_command('/mcp call default filesystem__echo {"message": "test"}')
        assert "Tool Execution Succeeded:" in out_approve.getvalue()
        assert "test echo result" in out_approve.getvalue()
