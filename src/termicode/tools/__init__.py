"""Tool subsystem for TermiCode AI: base classes, risk levels, and registry."""

from termicode.tools.base import (
    BaseTool,
    DuplicateToolError,
    RegistryError,
    RiskLevel,
    ToolArgumentError,
    ToolAuditMetadata,
    ToolContext,
    ToolError,
    ToolExecutionError,
    ToolResult,
    UnknownToolError,
)
from termicode.tools.command import RunCommandArgs, RunCommandTool
from termicode.tools.filesystem import (
    ApplyPatchArgs,
    ApplyPatchTool,
    ListFilesArgs,
    ListFilesTool,
    ReadFileArgs,
    ReadFileTool,
    SearchTextArgs,
    SearchTextTool,
    WriteFileArgs,
    WriteFileTool,
)
from termicode.tools.registry import ToolRegistry, create_default_registry

__all__ = [
    "ApplyPatchArgs",
    "ApplyPatchTool",
    "BaseTool",
    "DuplicateToolError",
    "ListFilesArgs",
    "ListFilesTool",
    "ReadFileArgs",
    "ReadFileTool",
    "RegistryError",
    "RiskLevel",
    "RunCommandArgs",
    "RunCommandTool",
    "SearchTextArgs",
    "SearchTextTool",
    "ToolArgumentError",
    "ToolAuditMetadata",
    "ToolContext",
    "ToolError",
    "ToolExecutionError",
    "ToolRegistry",
    "ToolResult",
    "UnknownToolError",
    "WriteFileArgs",
    "WriteFileTool",
    "create_default_registry",
]
