"""Typed registry for explicit tool registration, validation, and execution."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel

from termicode.tools.base import (
    BaseTool,
    DuplicateToolError,
    RiskLevel,
    ToolContext,
    ToolResult,
    UnknownToolError,
)


class ToolRegistry:
    """Explicit, in-memory registry holding registered tools and their schemas.

    Does not support arbitrary dynamic imports or unvalidated execution.
    Every tool proposal must resolve to an explicitly registered BaseTool instance.
    """

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool[Any]] = {}

    def register(self, tool: BaseTool[Any]) -> None:
        """Register an explicit BaseTool instance.

        Raises:
            TypeError: If the tool is not an instance of BaseTool.
            ValueError: If the tool name is missing or invalid.
            DuplicateToolError: If a tool with the same name is already registered.
        """
        if not isinstance(tool, BaseTool):
            raise TypeError(f"Expected BaseTool instance, got {type(tool).__name__}")

        if not tool.name or not tool.name.strip():
            raise ValueError("Tool name must be a non-empty string.")

        name = tool.name.strip()
        if name in self._tools:
            raise DuplicateToolError(f"Tool '{name}' is already registered in this registry.")

        self._tools[name] = tool

    def get(self, name: str) -> BaseTool[Any]:
        """Retrieve a registered tool by name.

        Raises:
            UnknownToolError: If the tool is not registered.
        """
        if name not in self._tools:
            raise UnknownToolError(
                f"Tool '{name}' is not registered. Registered tools: {list(self._tools.keys())}"
            )
        return self._tools[name]

    def has(self, name: str) -> bool:
        """Check whether a tool with the given name is registered."""
        return name in self._tools

    def list_tools(self) -> list[BaseTool[Any]]:
        """Return a list of all registered tools."""
        return list(self._tools.values())

    def list_names(self) -> list[str]:
        """Return sorted names of all registered tools."""
        return sorted(self._tools.keys())

    def get_schemas(self) -> list[dict[str, Any]]:
        """Return JSON schemas for all registered tools, suitable for model tool declarations."""
        return [tool.get_schema() for tool in self._tools.values()]

    def filter_by_risk(self, levels: Iterable[RiskLevel]) -> list[BaseTool[Any]]:
        """Return registered tools matching any of the specified risk levels."""
        target_levels = set(levels)
        return [tool for tool in self._tools.values() if tool.risk_level in target_levels]

    def execute(
        self,
        name: str,
        arguments: dict[str, Any] | BaseModel,
        context: ToolContext | None = None,
        raise_on_error: bool = False,
    ) -> ToolResult:
        """Synchronously execute a registered tool by name with strict validation."""
        if not self.has(name):
            if raise_on_error:
                raise UnknownToolError(
                    f"Tool '{name}' is not registered. Registered tools: {list(self._tools.keys())}"
                )
            return ToolResult(
                ok=False,
                output="",
                changed_files=[],
                error=f"Unknown tool '{name}'. Valid tools: {self.list_names()}",
                metadata={"tool_name": name},
            )

        tool = self.get(name)
        return tool.run(arguments, context=context, raise_on_error=raise_on_error)

    async def aexecute(
        self,
        name: str,
        arguments: dict[str, Any] | BaseModel,
        context: ToolContext | None = None,
        raise_on_error: bool = False,
    ) -> ToolResult:
        """Asynchronously execute a registered tool by name with strict validation."""
        if not self.has(name):
            if raise_on_error:
                raise UnknownToolError(
                    f"Tool '{name}' is not registered. Registered tools: {list(self._tools.keys())}"
                )
            return ToolResult(
                ok=False,
                output="",
                changed_files=[],
                error=f"Unknown tool '{name}'. Valid tools: {self.list_names()}",
                metadata={"tool_name": name},
            )

        tool = self.get(name)
        return await tool.arun(arguments, context=context, raise_on_error=raise_on_error)


def create_default_registry() -> ToolRegistry:
    """Create and return a ToolRegistry pre-populated with all standard built-in tools."""
    from termicode.tools.command import RunCommandTool
    from termicode.tools.filesystem import (
        ApplyPatchTool,
        ListFilesTool,
        ReadFileTool,
        SearchTextTool,
        WriteFileTool,
    )

    registry = ToolRegistry()
    registry.register(ListFilesTool())
    registry.register(ReadFileTool())
    registry.register(SearchTextTool())
    registry.register(WriteFileTool())
    registry.register(ApplyPatchTool())
    registry.register(RunCommandTool())
    return registry
