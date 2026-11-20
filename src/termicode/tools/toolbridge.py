"""ToolBridge MCP Gateway client, server manager, and dynamic tool integration for TermiCode AI.

Defines the interaction interface, persistent server configuration, and typed BaseTool
wrappers for communicating with the ToolBridge Enterprise Gateway (FastAPI / MCP).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field, create_model

from termicode.tools.base import (
    BaseTool,
    RiskLevel,
    ToolContext,
    ToolError,
    ToolResult,
)

if TYPE_CHECKING:
    from termicode.tools.registry import ToolRegistry

logger = logging.getLogger("termicode.tools.toolbridge")


class ToolBridgeError(ToolError):
    """Base exception for all ToolBridge gateway communication errors."""


class ToolBridgeConnectionError(ToolBridgeError):
    """Raised when ToolBridge API server cannot be reached."""


class ToolBridgeAuthError(ToolBridgeError):
    """Raised when authentication fails with ToolBridge (HTTP 401 / 403)."""


class ToolBridgeRateLimitError(ToolBridgeError):
    """Raised when request rate limit is exceeded on ToolBridge (HTTP 429)."""


class ToolBridgeExecutionError(ToolBridgeError):
    """Raised when tool execution fails on ToolBridge (HTTP 400 / 500)."""


@dataclass
class ToolBridgeConfig:
    """Connection parameters for ToolBridge Enterprise Gateway."""

    api_url: str = "http://127.0.0.1:8000"
    api_key: str | None = None
    role: str = "operator"
    timeout_seconds: float = 30.0


class ToolBridgeClientStub:
    """Client contract and HTTP adapter for communicating with ToolBridge Gateway.

    Implements synchronous HTTP REST calls using Python's standard library
    urllib.request, introducing zero external network dependencies to TermiCode AI.
    """

    def __init__(self, config: ToolBridgeConfig | None = None) -> None:
        self.config = config or ToolBridgeConfig()
        self.base_url = self.config.api_url.rstrip("/")

    def _build_request(
        self,
        endpoint: str,
        method: str = "GET",
        data: dict[str, Any] | None = None,
    ) -> Request:
        """Construct an authenticated urllib Request object."""
        url = f"{self.base_url}{endpoint}"
        encoded_data = json.dumps(data).encode("utf-8") if data is not None else None
        headers: dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": "TermiCode-AI/0.1.0",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        if self.config.api_key:
            headers["X-API-Key"] = self.config.api_key.strip()

        return Request(url=url, data=encoded_data, headers=headers, method=method)

    def check_health(self) -> dict[str, Any]:
        """Verify connectivity and health of the ToolBridge service.

        Returns:
            Dict containing service status and active uptime.
        """
        req = self._build_request("/health", method="GET")
        try:
            with urlopen(req, timeout=self.config.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
                return payload if isinstance(payload, dict) else {"status": "healthy", "ok": True}
        except URLError as err:
            raise ToolBridgeConnectionError(
                f"Failed to connect to ToolBridge at {self.base_url}: {err}"
            ) from err

    def list_tools(self) -> list[dict[str, Any]]:
        """Query ToolBridge for all exposed namespaced MCP tools.

        Returns:
            List of tool specifications with names, descriptions, and input schemas.
        """
        req = self._build_request("/api/v1/tools", method="GET")
        try:
            with urlopen(req, timeout=self.config.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
                if isinstance(body, dict) and "tools" in body:
                    return list(body["tools"])
                return []
        except HTTPError as err:
            if err.code in (401, 403):
                raise ToolBridgeAuthError(
                    f"Authentication failed querying ToolBridge tools (HTTP {err.code}). "
                    "Check TERMICODE_TOOLBRIDGE_API_KEY."
                ) from err
            if err.code == 429:
                raise ToolBridgeRateLimitError(
                    "Rate limit exceeded on ToolBridge gateway."
                ) from err
            raise ToolBridgeError(f"HTTP error {err.code} from ToolBridge: {err.reason}") from err
        except URLError as err:
            raise ToolBridgeConnectionError(
                f"Failed to reach ToolBridge at {self.base_url}: {err}"
            ) from err

    def invoke_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute a namespaced MCP tool through ToolBridge.

        Args:
            tool_name: Namespaced tool identifier (e.g. 'filesystem__read_file').
            arguments: Arguments payload matching tool's JSON schema.

        Returns:
            Execution result dictionary containing status, tool_name, and result text.
        """
        payload = {"tool_name": tool_name, "arguments": arguments}
        req = self._build_request("/api/v1/tools/invoke", method="POST", data=payload)
        try:
            with urlopen(req, timeout=self.config.timeout_seconds) as response:
                res = json.loads(response.read().decode("utf-8"))
                return res if isinstance(res, dict) else {"result": str(res)}
        except HTTPError as err:
            err_body = ""
            try:
                err_body = err.read().decode("utf-8")
            except Exception:
                pass
            if err.code in (401, 403):
                msg = f"Authorization denied by ToolBridge (HTTP {err.code}): {err_body}"
                raise ToolBridgeAuthError(msg) from err
            if err.code == 429:
                msg = f"Rate limit exceeded on ToolBridge gateway (HTTP 429): {err_body}"
                raise ToolBridgeRateLimitError(msg) from err
            detail = err_body or err.reason
            msg = f"ToolBridge invocation of '{tool_name}' failed (HTTP {err.code}): {detail}"
            raise ToolBridgeExecutionError(msg) from err
        except URLError as err:
            raise ToolBridgeConnectionError(
                f"Failed to connect to ToolBridge during tool invocation: {err}"
            ) from err


# Primary client alias
ToolBridgeClient = ToolBridgeClientStub


class ToolBridgeServerManager:
    """Manages configured ToolBridge upstream servers and tool discovery per workspace."""

    def __init__(self, workspace_root: Path | str) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.config_file = self.workspace_root / ".termicode" / "toolbridge_servers.json"
        self._servers: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        """Load servers from configuration file or initialize default."""
        if self.config_file.exists():
            try:
                data = json.loads(self.config_file.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "servers" in data:
                    self._servers = dict(data["servers"])
                    return
            except Exception:
                self._servers = {}

        # Default configuration fallback
        default_url = "http://127.0.0.1:8000"
        default_key = None
        try:
            from termicode.config import Settings

            settings = Settings.load(workspace=self.workspace_root)
            default_url = settings.toolbridge_api_url or default_url
            default_key = settings.toolbridge_api_key
        except Exception:
            pass

        self._servers = {
            "default": {
                "name": "default",
                "url": default_url,
                "token": default_key,
            }
        }
        self._save()

    def _save(self) -> None:
        """Persist servers dictionary to .termicode/toolbridge_servers.json."""
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {"servers": self._servers}
        self.config_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def add_server(self, name: str, url: str, token: str | None = None) -> dict[str, Any]:
        """Register or update a ToolBridge server configuration."""
        norm_name = name.strip()
        norm_url = url.strip().rstrip("/")
        if not norm_name:
            raise ValueError("Server name cannot be empty.")
        if not norm_url.startswith(("http://", "https://")):
            raise ValueError(f"Invalid URL '{url}'. Must start with 'http://' or 'https://'.")

        record = {
            "name": norm_name,
            "url": norm_url,
            "token": token.strip() if token else None,
        }
        self._servers[norm_name] = record
        self._save()
        return record

    def list_servers(self) -> list[dict[str, Any]]:
        """Return list of all configured ToolBridge servers."""
        return list(self._servers.values())

    def get_server(self, name: str) -> dict[str, Any] | None:
        """Retrieve server record by name."""
        return self._servers.get(name.strip())

    def remove_server(self, name: str) -> bool:
        """Remove a configured server by name."""
        norm_name = name.strip()
        if norm_name in self._servers:
            del self._servers[norm_name]
            self._save()
            return True
        return False

    def get_client(self, name: str | None = None) -> ToolBridgeClientStub:
        """Return a configured ToolBridge client for the specified or default server."""
        target_name = (name or "default").strip()
        server_info = self._servers.get(target_name)
        if not server_info:
            if self._servers:
                first_key = next(iter(self._servers))
                server_info = self._servers[first_key]
            else:
                server_info = {"name": "default", "url": "http://127.0.0.1:8000", "token": None}

        config = ToolBridgeConfig(
            api_url=server_info.get("url", "http://127.0.0.1:8000"),
            api_key=server_info.get("token"),
        )
        return ToolBridgeClientStub(config)

    def discover_tools(self, server_name: str | None = None) -> list[dict[str, Any]]:
        """Query ToolBridge server(s) for available tools."""
        results: list[dict[str, Any]] = []
        targets = [server_name] if server_name else list(self._servers.keys())

        for s_name in targets:
            if not s_name or s_name not in self._servers:
                continue
            client = self.get_client(s_name)
            try:
                server_tools = client.list_tools()
                for t in server_tools:
                    item = dict(t)
                    item["server_name"] = s_name
                    results.append(item)
            except ToolBridgeError as err:
                logger.warning(f"Could not discover tools from ToolBridge server '{s_name}': {err}")

        return results

    def inspect_tool(
        self,
        tool_name: str,
        server_name: str | None = None,
    ) -> dict[str, Any] | None:
        """Retrieve tool metadata and input schema for a specific tool."""
        all_tools = self.discover_tools(server_name=server_name)
        target = tool_name.strip()
        for t in all_tools:
            if t.get("namespaced_name") == target or t.get("original_name") == target:
                return t
        return None

    def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a tool through the designated server client."""
        client = self.get_client(server_name)
        return client.invoke_tool(tool_name, arguments)


def create_pydantic_schema_from_json(
    schema_name: str,
    json_schema: dict[str, Any] | None,
) -> type[BaseModel]:
    """Dynamically generate a typed Pydantic BaseModel class from a tool JSON Schema.

    Enables strict Pydantic argument validation for remote MCP tools.
    """
    if not json_schema or not isinstance(json_schema, dict):
        base_cfg = ConfigDict(extra="allow")
        return create_model(schema_name, __config__=base_cfg)

    properties = json_schema.get("properties", {})
    required_fields = set(json_schema.get("required", []))
    fields: dict[str, Any] = {}

    type_mapping: dict[str, type] = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
        "object": dict,
    }

    for prop_name, prop_spec in properties.items():
        if not isinstance(prop_spec, dict):
            continue
        type_str = prop_spec.get("type", "string")
        py_type: Any = type_mapping.get(type_str, Any)
        description = prop_spec.get("description", "")

        is_required = prop_name in required_fields
        default_val = prop_spec.get("default", ... if is_required else None)

        if not is_required and default_val is ...:
            default_val = None
            py_type = py_type | None

        fields[prop_name] = (py_type, Field(default=default_val, description=description))

    base_cfg = ConfigDict(extra="allow")
    return create_model(schema_name, __config__=base_cfg, **fields)


class MCPTool(BaseTool[BaseModel]):
    """Dynamic BaseTool wrapper encapsulating a remote MCP tool from ToolBridge."""

    is_mcp: bool = True

    def __init__(
        self,
        server_name: str,
        tool_spec: dict[str, Any],
        client: ToolBridgeClientStub,
    ) -> None:
        self.server_name = server_name
        self.tool_spec = tool_spec
        self.client = client

        raw_name = tool_spec.get("namespaced_name") or tool_spec.get("name") or "unnamed_mcp_tool"
        self.name = raw_name
        self.original_name = tool_spec.get("original_name") or raw_name

        desc = tool_spec.get("description") or f"Remote MCP tool from server '{server_name}'."
        self.description = f"[MCP Tool from '{server_name}'] {desc}"

        tier = tool_spec.get("sensitivity_tier", "SAFE")
        if tier == "SENSITIVE":
            self.risk_level = RiskLevel.EXECUTE
        else:
            self.risk_level = RiskLevel.READ

        # Dynamically compile Pydantic schema
        clean_model_name = (
            "".join(c.capitalize() for c in self.name.replace("__", "_").split("_")) + "Args"
        )
        self.args_schema = create_pydantic_schema_from_json(
            clean_model_name,
            tool_spec.get("input_schema"),
        )

    def _execute(
        self,
        args: BaseModel,
        context: ToolContext | None = None,
    ) -> ToolResult:
        """Execute the remote MCP tool via ToolBridge client."""
        # 1. Approval Gate Enforcement:
        # All MCP tools require explicit confirmation before invocation
        if context is not None and not context.user_confirmed:
            return ToolResult(
                ok=False,
                output="",
                error=(
                    f"Action aborted: User confirmation required to execute "
                    f"remote MCP tool '{self.name}' on server '{self.server_name}'."
                ),
                metadata={"tool_name": self.name, "approval_required": True, "is_mcp": True},
            )

        # 2. Invoke remote ToolBridge
        payload = args.model_dump(exclude_unset=False)
        try:
            res = self.client.invoke_tool(self.name, payload)
            output_text = str(res.get("result", res))
            return ToolResult(
                ok=True,
                output=output_text,
                metadata={
                    "tool_name": self.name,
                    "server_name": self.server_name,
                    "duration_ms": res.get("duration_ms", 0.0),
                    "gateway": "ToolBridge",
                    "is_mcp": True,
                },
            )
        except ToolBridgeError as err:
            return ToolResult(
                ok=False,
                output="",
                error=f"ToolBridge invocation error on '{self.name}': {err}",
                metadata={
                    "tool_name": self.name,
                    "error_type": type(err).__name__,
                    "is_mcp": True,
                },
            )


def discover_and_register_mcp_tools(
    registry: ToolRegistry,
    workspace_root: Path | str,
) -> list[str]:
    """Discover tools from all configured ToolBridge servers and register them into ToolRegistry.

    Returns:
        List of registered MCP tool names.
    """
    manager = ToolBridgeServerManager(workspace_root)
    registered_names: list[str] = []

    for server in manager.list_servers():
        s_name = server["name"]
        client = manager.get_client(s_name)
        try:
            tools = client.list_tools()
            for t_spec in tools:
                mcp_tool = MCPTool(server_name=s_name, tool_spec=t_spec, client=client)
                registry.register_or_replace(mcp_tool)
                registered_names.append(mcp_tool.name)
        except Exception as err:
            logger.debug(
                f"Could not connect to ToolBridge server '{s_name}' during discovery: {err}"
            )

    return registered_names


class ToolBridgeStubArgs(BaseModel):
    """Input arguments for ToolBridge scaffolding stub."""

    tool_name: str = Field(
        ...,
        description="Namespaced tool name exposed by ToolBridge (e.g. 'postgres__execute_query').",
    )
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        description="Dictionary of arguments matching the target tool schema.",
    )


class ToolBridgeStubTool(BaseTool[ToolBridgeStubArgs]):
    """Scaffolding tool illustrating remote ToolBridge tool invocation within TermiCode."""

    name = "toolbridge_invoke"
    description = (
        "Invoke a remote MCP tool managed by the ToolBridge Enterprise Gateway. "
        "Allows executing PostgreSQL queries, GitHub repository inspection, and external MCP tools."
    )
    risk_level = RiskLevel.EXECUTE
    args_schema = ToolBridgeStubArgs

    def __init__(self, client: ToolBridgeClientStub | None = None) -> None:
        self.client = client or ToolBridgeClientStub()

    def _execute(
        self,
        args: ToolBridgeStubArgs,
        context: ToolContext | None = None,
    ) -> ToolResult:
        """Execute the remote tool call via ToolBridge client stub."""
        try:
            result = self.client.invoke_tool(args.tool_name, args.arguments)
            output_text = str(result.get("result", result))
            return ToolResult(
                ok=True,
                output=output_text,
                metadata={
                    "tool_name": args.tool_name,
                    "duration_ms": result.get("duration_ms", 0.0),
                    "gateway": "ToolBridge",
                },
            )
        except ToolBridgeError as err:
            return ToolResult(
                ok=False,
                output="",
                error=str(err),
                metadata={"tool_name": args.tool_name, "error_type": type(err).__name__},
            )
