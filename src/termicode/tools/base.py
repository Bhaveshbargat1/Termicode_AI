"""Base primitives, result structures, and execution context for TermiCode tools."""

from __future__ import annotations

import asyncio
import inspect
import time
from abc import ABC
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field, ValidationError

from termicode.workspace import WorkspaceBoundary


class RiskLevel(StrEnum):
    """Categorized risk level for tool operations."""

    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"


class ToolError(Exception):
    """Base exception for all tool-related errors."""


class RegistryError(ToolError):
    """Base exception for tool registry operations."""


class DuplicateToolError(RegistryError):
    """Raised when registering a tool whose name is already registered."""


class UnknownToolError(RegistryError):
    """Raised when requesting a tool name that does not exist in the registry."""


class ToolArgumentError(ToolError):
    """Raised when arguments fail Pydantic schema validation."""


class ToolExecutionError(ToolError):
    """Raised when an unhandled error occurs during tool execution."""


@dataclass
class ToolContext:
    """Runtime context provided to tools during execution."""

    workspace: WorkspaceBoundary | None = None
    step_index: int = 0
    run_id: str | None = None
    user_confirmed: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


class ToolAuditMetadata(BaseModel):
    """Structured audit trail captured for every tool proposal execution."""

    tool_name: str
    risk_level: str
    started_at: str
    finished_at: str
    duration_ms: float
    input_arguments: dict[str, Any] = Field(default_factory=dict)
    extra: dict[str, Any] = Field(default_factory=dict)


def _normalize_paths(items: Sequence[str | Path] | None) -> list[str]:
    """Normalize a sequence of strings and Paths into POSIX-style clean string paths."""
    if not items:
        return []
    normalized: list[str] = []
    for item in items:
        if isinstance(item, Path):
            normalized.append(item.as_posix())
        else:
            normalized.append(str(item).replace("\\", "/"))
    return normalized


class ToolResult(BaseModel):
    """Normalized result returned from all tool executions."""

    ok: bool = Field(description="Whether the tool action completed successfully.")
    output: str = Field(default="", description="Text output, summaries, or file contents.")
    changed_files: list[str] = Field(
        default_factory=list,
        description="List of workspace files modified or created during tool execution.",
    )
    error: str | None = Field(
        default=None,
        description="Error description if the execution failed.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Audit metadata and execution telemetry.",
    )

    def __init__(
        self,
        ok: bool,
        output: str = "",
        changed_files: Sequence[str | Path] | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        normalized_files = _normalize_paths(changed_files)
        super().__init__(
            ok=ok,
            output=output,
            changed_files=normalized_files,
            error=error,
            metadata=metadata or {},
            **kwargs,
        )


ArgsT = TypeVar("ArgsT", bound=BaseModel)


class BaseTool(ABC, Generic[ArgsT]):
    """Abstract base class for typed TermiCode AI tools.

    Tools define a stable name, description, risk level, Pydantic argument schema,
    and a sync or async execution method. Tools never execute unvalidated inputs.
    """

    name: str
    description: str
    risk_level: RiskLevel
    args_schema: type[ArgsT]

    @property
    def is_async(self) -> bool:
        """Returns True if the tool implements a native coroutine execution method."""
        return inspect.iscoroutinefunction(self._aexecute) and (
            self._aexecute.__code__ is not BaseTool._aexecute.__code__
        )

    def validate_args(self, raw_args: dict[str, Any] | BaseModel) -> ArgsT:
        """Validate input arguments against this tool's Pydantic schema."""
        if isinstance(raw_args, self.args_schema):
            return raw_args

        if isinstance(raw_args, BaseModel):
            raw_args = raw_args.model_dump()

        if isinstance(raw_args, dict):
            try:
                return self.args_schema.model_validate(raw_args)
            except ValidationError as err:
                raise ToolArgumentError(
                    f"Arguments failed validation for tool '{self.name}': {err}"
                ) from err

        raise ToolArgumentError(
            f"Expected dict or {self.args_schema.__name__} for tool '{self.name}', "
            f"got {type(raw_args).__name__}"
        )

    def get_schema(self) -> dict[str, Any]:
        """Return the JSON schema representation for model tool definitions."""
        return {
            "name": self.name,
            "description": self.description,
            "risk_level": self.risk_level.value,
            "parameters": self.args_schema.model_json_schema(),
        }

    def _execute(self, args: ArgsT, context: ToolContext | None = None) -> ToolResult:
        """Internal synchronous execution implementation.

        Subclasses implementing sync tools should override this method.
        """
        raise NotImplementedError(f"Tool '{self.name}' does not implement synchronous execution.")

    async def _aexecute(self, args: ArgsT, context: ToolContext | None = None) -> ToolResult:
        """Internal asynchronous execution implementation.

        Subclasses implementing async tools should override this method.
        By default, runs sync _execute in a worker thread.
        """
        return await asyncio.to_thread(self._execute, args, context)

    def run(
        self,
        raw_args: dict[str, Any] | BaseModel,
        context: ToolContext | None = None,
        raise_on_error: bool = False,
    ) -> ToolResult:
        """Synchronously execute the tool with full validation, normalization, and auditing."""
        start_mono = time.perf_counter()
        started_at = datetime.now(UTC).isoformat()
        serialized_args: dict[str, Any] = {}

        try:
            validated = self.validate_args(raw_args)
            serialized_args = validated.model_dump()

            # Execute tool implementation
            if self.is_async:
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    result = asyncio.run(self._aexecute(validated, context))
                else:
                    raise RuntimeError(
                        f"Cannot invoke async tool '{self.name}' synchronously "
                        "while an event loop is running. Use arun() instead."
                    )
            else:
                result = self._execute(validated, context)

        except ToolArgumentError as err:
            if raise_on_error:
                raise
            return self._build_error_result(
                error_message=str(err),
                started_at=started_at,
                start_mono=start_mono,
                raw_args=serialized_args,
                context=context,
            )
        except Exception as err:
            if raise_on_error:
                raise ToolExecutionError(f"Unhandled error in tool '{self.name}': {err}") from err
            return self._build_error_result(
                error_message=f"{type(err).__name__}: {err}",
                started_at=started_at,
                start_mono=start_mono,
                raw_args=serialized_args,
                context=context,
            )

        return self._enrich_result(result, started_at, start_mono, serialized_args, context)

    async def arun(
        self,
        raw_args: dict[str, Any] | BaseModel,
        context: ToolContext | None = None,
        raise_on_error: bool = False,
    ) -> ToolResult:
        """Asynchronously execute the tool with full validation, normalization, and auditing."""
        start_mono = time.perf_counter()
        started_at = datetime.now(UTC).isoformat()
        serialized_args: dict[str, Any] = {}

        try:
            validated = self.validate_args(raw_args)
            serialized_args = validated.model_dump()
            result = await self._aexecute(validated, context)
        except ToolArgumentError as err:
            if raise_on_error:
                raise
            return self._build_error_result(
                error_message=str(err),
                started_at=started_at,
                start_mono=start_mono,
                raw_args=serialized_args,
                context=context,
            )
        except Exception as err:
            if raise_on_error:
                raise ToolExecutionError(f"Unhandled error in tool '{self.name}': {err}") from err
            return self._build_error_result(
                error_message=f"{type(err).__name__}: {err}",
                started_at=started_at,
                start_mono=start_mono,
                raw_args=serialized_args,
                context=context,
            )

        return self._enrich_result(result, started_at, start_mono, serialized_args, context)

    def _enrich_result(
        self,
        result: ToolResult,
        started_at: str,
        start_mono: float,
        args_dict: dict[str, Any],
        context: ToolContext | None,
    ) -> ToolResult:
        """Attach audit metadata to a completed ToolResult."""
        duration_ms = round((time.perf_counter() - start_mono) * 1000, 3)
        finished_at = datetime.now(UTC).isoformat()

        audit = ToolAuditMetadata(
            tool_name=self.name,
            risk_level=self.risk_level.value,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            input_arguments=args_dict,
            extra={
                "step_index": context.step_index if context else 0,
                "run_id": context.run_id if context else None,
                "user_confirmed": context.user_confirmed if context else False,
            },
        )

        metadata = dict(result.metadata)
        metadata["audit"] = audit.model_dump()

        return ToolResult(
            ok=result.ok,
            output=result.output,
            changed_files=result.changed_files,
            error=result.error,
            metadata=metadata,
        )

    def _build_error_result(
        self,
        error_message: str,
        started_at: str,
        start_mono: float,
        raw_args: dict[str, Any],
        context: ToolContext | None,
    ) -> ToolResult:
        """Construct a standardized ToolResult for validation or runtime failures."""
        duration_ms = round((time.perf_counter() - start_mono) * 1000, 3)
        finished_at = datetime.now(UTC).isoformat()

        audit = ToolAuditMetadata(
            tool_name=self.name,
            risk_level=self.risk_level.value,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            input_arguments=raw_args,
            extra={
                "step_index": context.step_index if context else 0,
                "run_id": context.run_id if context else None,
                "user_confirmed": context.user_confirmed if context else False,
            },
        )

        return ToolResult(
            ok=False,
            output="",
            changed_files=[],
            error=error_message,
            metadata={"audit": audit.model_dump()},
        )
