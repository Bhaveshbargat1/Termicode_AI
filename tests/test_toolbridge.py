"""Unit tests for ToolBridge client scaffolding, config, and stub tool in TermiCode AI."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

from termicode.tools import RiskLevel, ToolRegistry
from termicode.tools.toolbridge import (
    ToolBridgeAuthError,
    ToolBridgeClientStub,
    ToolBridgeConfig,
    ToolBridgeConnectionError,
    ToolBridgeRateLimitError,
    ToolBridgeStubTool,
)


def test_toolbridge_config_defaults() -> None:
    config = ToolBridgeConfig()
    assert config.api_url == "http://127.0.0.1:8000"
    assert config.api_key is None
    assert config.role == "operator"
    assert config.timeout_seconds == 30.0


def test_toolbridge_client_check_health_success() -> None:
    client = ToolBridgeClientStub()
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"status": "healthy"}).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("termicode.tools.toolbridge.urlopen", return_value=mock_resp):
        res = client.check_health()
        assert res == {"status": "healthy"}


def test_toolbridge_client_check_health_connection_error() -> None:
    client = ToolBridgeClientStub()
    with patch("termicode.tools.toolbridge.urlopen", side_effect=URLError("Connection refused")):
        with pytest.raises(ToolBridgeConnectionError, match="Failed to connect"):
            client.check_health()


def test_toolbridge_client_list_tools_success() -> None:
    client = ToolBridgeClientStub(ToolBridgeConfig(api_key="test-key-32chars-long-placeholder"))
    sample_tools = [{"namespaced_name": "postgres__execute_query", "description": "Execute SQL"}]
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"tools": sample_tools, "total": 1}).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("termicode.tools.toolbridge.urlopen", return_value=mock_resp):
        tools = client.list_tools()
        assert len(tools) == 1
        assert tools[0]["namespaced_name"] == "postgres__execute_query"


def test_toolbridge_client_list_tools_auth_error() -> None:
    client = ToolBridgeClientStub()
    http_err = HTTPError("http://127.0.0.1:8000/api/v1/tools", 403, "Forbidden", {}, None)  # type: ignore[arg-type]
    with patch("termicode.tools.toolbridge.urlopen", side_effect=http_err):
        with pytest.raises(ToolBridgeAuthError, match="Authentication failed"):
            client.list_tools()


def test_toolbridge_client_list_tools_rate_limit() -> None:
    client = ToolBridgeClientStub()
    http_err = HTTPError("http://127.0.0.1:8000/api/v1/tools", 429, "Too Many Requests", {}, None)  # type: ignore[arg-type]
    with patch("termicode.tools.toolbridge.urlopen", side_effect=http_err):
        with pytest.raises(ToolBridgeRateLimitError, match="Rate limit exceeded"):
            client.list_tools()


def test_toolbridge_client_invoke_tool_success() -> None:
    client = ToolBridgeClientStub()
    expected_result = {
        "status": "success",
        "tool_name": "postgres__execute_query",
        "duration_ms": 15.2,
        "result": "[{'count': 42}]",
    }
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(expected_result).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("termicode.tools.toolbridge.urlopen", return_value=mock_resp):
        res = client.invoke_tool("postgres__execute_query", {"query": "SELECT count(*) FROM users"})
        assert res["status"] == "success"
        assert res["tool_name"] == "postgres__execute_query"


def test_toolbridge_stub_tool_metadata_and_execution() -> None:
    mock_client = MagicMock(spec=ToolBridgeClientStub)
    mock_client.invoke_tool.return_value = {
        "status": "success",
        "tool_name": "github__list_issues",
        "result": "Issue #1: Fix bug",
        "duration_ms": 45.0,
    }

    tool = ToolBridgeStubTool(client=mock_client)
    assert tool.name == "toolbridge_invoke"
    assert tool.risk_level == RiskLevel.EXECUTE
    schema = tool.get_schema()
    assert "parameters" in schema

    # Execute successfully
    res = tool.run(
        {"tool_name": "github__list_issues", "arguments": {"owner": "test", "repo": "test"}}
    )
    assert res.ok is True
    assert "Issue #1: Fix bug" in res.output
    assert res.metadata["gateway"] == "ToolBridge"

    # Execution failure handling
    mock_client.invoke_tool.side_effect = ToolBridgeAuthError("API key required")
    fail_res = tool.run({"tool_name": "github__list_issues", "arguments": {}})
    assert fail_res.ok is False
    assert "API key required" in str(fail_res.error)


def test_toolbridge_stub_tool_registers_in_registry() -> None:
    registry = ToolRegistry()
    stub_tool = ToolBridgeStubTool()
    registry.register(stub_tool)

    assert registry.has("toolbridge_invoke")
    retrieved = registry.get("toolbridge_invoke")
    assert retrieved.name == "toolbridge_invoke"
    assert retrieved.risk_level == RiskLevel.EXECUTE
