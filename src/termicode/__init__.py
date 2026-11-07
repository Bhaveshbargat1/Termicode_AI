"""TermiCode AI -- Safe, bounded terminal coding assistant."""

from termicode.config import Settings
from termicode.tools import (
    BaseTool,
    DuplicateToolError,
    RegistryError,
    RiskLevel,
    ToolArgumentError,
    ToolContext,
    ToolError,
    ToolExecutionError,
    ToolRegistry,
    ToolResult,
    UnknownToolError,
)
from termicode.workspace import (
    SensitivePathError,
    WorkspaceBoundary,
    WorkspaceError,
    WorkspaceEscapeError,
    WorkspaceNotADirectoryError,
    WorkspaceNotFoundError,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "BaseTool",
    "DuplicateToolError",
    "RegistryError",
    "RiskLevel",
    "Settings",
    "ToolArgumentError",
    "ToolContext",
    "ToolError",
    "ToolExecutionError",
    "ToolRegistry",
    "ToolResult",
    "UnknownToolError",
    "WorkspaceBoundary",
    "WorkspaceError",
    "WorkspaceEscapeError",
    "WorkspaceNotADirectoryError",
    "WorkspaceNotFoundError",
    "SensitivePathError",
]
