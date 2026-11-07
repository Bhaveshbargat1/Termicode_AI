"""Tests for typed Tool Registry, schema validation, execution, and audit metadata."""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, Field

from termicode.tools import (
    BaseTool,
    DuplicateToolError,
    RiskLevel,
    ToolArgumentError,
    ToolContext,
    ToolExecutionError,
    ToolRegistry,
    ToolResult,
    UnknownToolError,
)

# --- Concrete test tools ---


class ReadFileArgs(BaseModel):
    path: str
    max_bytes: int = Field(default=1024, ge=1)


class MockReadFileTool(BaseTool[ReadFileArgs]):
    name = "read_file"
    description = "Read file contents from workspace."
    risk_level = RiskLevel.READ
    args_schema = ReadFileArgs

    def _execute(self, args: ReadFileArgs, context: ToolContext | None = None) -> ToolResult:
        if args.path == "fail.txt":
            raise ValueError("Simulated read failure.")
        return ToolResult(
            ok=True,
            output=f"Contents of {args.path} (capped at {args.max_bytes} bytes)",
        )


class WriteFileArgs(BaseModel):
    path: str
    content: str


class MockWriteFileTool(BaseTool[WriteFileArgs]):
    name = "write_file"
    description = "Write text content to a workspace file."
    risk_level = RiskLevel.WRITE
    args_schema = WriteFileArgs

    def _execute(self, args: WriteFileArgs, context: ToolContext | None = None) -> ToolResult:
        return ToolResult(
            ok=True,
            output=f"Wrote {len(args.content)} bytes to {args.path}",
            changed_files=[Path(args.path)],  # Test Path normalization
        )


class RunTestsArgs(BaseModel):
    test_target: str = "tests/"


class MockAsyncRunTestsTool(BaseTool[RunTestsArgs]):
    name = "run_tests"
    description = "Asynchronously execute targeted pytest run."
    risk_level = RiskLevel.EXECUTE
    args_schema = RunTestsArgs

    async def _aexecute(self, args: RunTestsArgs, context: ToolContext | None = None) -> ToolResult:
        if args.test_target == "raise":
            raise RuntimeError("Test execution crashed.")
        return ToolResult(
            ok=True,
            output=f"Executed tests on {args.test_target}: all passed",
            metadata={"exit_code": 0},
        )


# --- Registry Tests ---


def test_registry_registration_and_lookup() -> None:
    registry = ToolRegistry()
    tool = MockReadFileTool()

    assert not registry.has("read_file")
    registry.register(tool)
    assert registry.has("read_file")
    assert registry.get("read_file") is tool
    assert registry.list_names() == ["read_file"]
    assert registry.list_tools() == [tool]


def test_registry_duplicate_name_rejected() -> None:
    registry = ToolRegistry()
    registry.register(MockReadFileTool())

    duplicate_tool = MockReadFileTool()
    with pytest.raises(DuplicateToolError) as exc_info:
        registry.register(duplicate_tool)

    assert "read_file" in str(exc_info.value)
    assert "already registered" in str(exc_info.value)


def test_registry_rejects_invalid_tool_instances() -> None:
    registry = ToolRegistry()
    with pytest.raises(TypeError, match="Expected BaseTool instance"):
        registry.register("not_a_tool")  # type: ignore[arg-type]


def test_registry_unknown_tool_lookup() -> None:
    registry = ToolRegistry()
    with pytest.raises(UnknownToolError) as exc_info:
        registry.get("non_existent")

    assert "non_existent" in str(exc_info.value)


def test_registry_execute_unknown_tool() -> None:
    registry = ToolRegistry()

    # raise_on_error=True raises UnknownToolError
    with pytest.raises(UnknownToolError):
        registry.execute("unknown", {}, raise_on_error=True)

    # raise_on_error=False normalizes into ToolResult(ok=False)
    result = registry.execute("unknown", {})
    assert result.ok is False
    assert "Unknown tool 'unknown'" in (result.error or "")


def test_registry_aexecute_unknown_tool() -> None:
    async def _test() -> None:
        registry = ToolRegistry()

        with pytest.raises(UnknownToolError):
            await registry.aexecute("unknown", {}, raise_on_error=True)

        result = await registry.aexecute("unknown", {})
        assert result.ok is False
        assert "Unknown tool 'unknown'" in (result.error or "")

    asyncio.run(_test())


# --- Argument Validation Tests ---


def test_tool_argument_validation_success() -> None:
    tool = MockReadFileTool()
    validated = tool.validate_args({"path": "src/main.py", "max_bytes": 500})
    assert validated.path == "src/main.py"
    assert validated.max_bytes == 500


def test_tool_argument_validation_missing_required_field() -> None:
    tool = MockReadFileTool()
    with pytest.raises(ToolArgumentError):
        tool.validate_args({})


def test_tool_argument_validation_invalid_type() -> None:
    tool = MockReadFileTool()
    with pytest.raises(ToolArgumentError):
        tool.validate_args({"path": "src/main.py", "max_bytes": "not_an_int"})


def test_tool_argument_validation_constraint_violation() -> None:
    tool = MockReadFileTool()
    # max_bytes must be ge=1
    with pytest.raises(ToolArgumentError):
        tool.validate_args({"path": "src/main.py", "max_bytes": 0})


def test_registry_execute_invalid_arguments() -> None:
    registry = ToolRegistry()
    registry.register(MockReadFileTool())

    # raise_on_error=True
    with pytest.raises(ToolArgumentError):
        registry.execute("read_file", {"max_bytes": -5}, raise_on_error=True)

    # raise_on_error=False returns normalized error result
    result = registry.execute("read_file", {"max_bytes": -5})
    assert result.ok is False
    assert result.output == ""
    assert "failed validation" in (result.error or "").lower()
    assert "audit" in result.metadata
    assert result.metadata["audit"]["tool_name"] == "read_file"


# --- Result Normalization & Execution Tests ---


def test_sync_execution_and_path_normalization() -> None:
    registry = ToolRegistry()
    registry.register(MockWriteFileTool())

    context = ToolContext(step_index=2, run_id="run-123", user_confirmed=True)
    result = registry.execute(
        "write_file",
        {"path": "docs/guide.md", "content": "# Hello"},
        context=context,
    )

    assert result.ok is True
    assert result.output == "Wrote 7 bytes to docs/guide.md"
    # Path object was normalized to string
    assert result.changed_files == ["docs/guide.md"]
    assert result.error is None

    # Check audit metadata
    audit = result.metadata["audit"]
    assert audit["tool_name"] == "write_file"
    assert audit["risk_level"] == "write"
    assert audit["duration_ms"] >= 0
    assert audit["input_arguments"]["path"] == "docs/guide.md"
    assert audit["extra"]["step_index"] == 2
    assert audit["extra"]["run_id"] == "run-123"
    assert audit["extra"]["user_confirmed"] is True


def test_sync_execution_exception_normalization() -> None:
    registry = ToolRegistry()
    registry.register(MockReadFileTool())

    # Without raise_on_error: caught and normalized
    result = registry.execute("read_file", {"path": "fail.txt"})
    assert result.ok is False
    assert "ValueError: Simulated read failure." in (result.error or "")
    assert result.metadata["audit"]["tool_name"] == "read_file"

    # With raise_on_error: raises ToolExecutionError
    with pytest.raises(ToolExecutionError, match="Simulated read failure."):
        registry.execute("read_file", {"path": "fail.txt"}, raise_on_error=True)


def test_async_tool_execution_normalization() -> None:
    async def _test() -> None:
        registry = ToolRegistry()
        async_tool = MockAsyncRunTestsTool()
        registry.register(async_tool)

        assert async_tool.is_async is True

        result = await registry.aexecute(
            "run_tests",
            {"test_target": "tests/test_unit.py"},
        )
        assert result.ok is True
        assert "Executed tests on tests/test_unit.py" in result.output
        assert result.metadata["exit_code"] == 0
        assert result.metadata["audit"]["tool_name"] == "run_tests"
        assert result.metadata["audit"]["risk_level"] == "execute"

    asyncio.run(_test())


def test_async_tool_exception_normalization() -> None:
    async def _test() -> None:
        registry = ToolRegistry()
        registry.register(MockAsyncRunTestsTool())

        # Normalized error result
        result = await registry.aexecute("run_tests", {"test_target": "raise"})
        assert result.ok is False
        assert "RuntimeError: Test execution crashed." in (result.error or "")

        # Raise on error
        with pytest.raises(ToolExecutionError, match="Test execution crashed."):
            await registry.aexecute("run_tests", {"test_target": "raise"}, raise_on_error=True)

    asyncio.run(_test())


def test_sync_tool_executed_via_async_aexecute() -> None:
    async def _test() -> None:
        registry = ToolRegistry()
        registry.register(MockReadFileTool())

        result = await registry.aexecute("read_file", {"path": "README.md"})
        assert result.ok is True
        assert "Contents of README.md" in result.output

    asyncio.run(_test())


# --- Risk Level Filtering and Schema Export Tests ---


def test_filter_by_risk() -> None:
    registry = ToolRegistry()
    read_tool = MockReadFileTool()
    write_tool = MockWriteFileTool()
    async_tool = MockAsyncRunTestsTool()

    registry.register(read_tool)
    registry.register(write_tool)
    registry.register(async_tool)

    read_only = registry.filter_by_risk([RiskLevel.READ])
    assert read_only == [read_tool]

    mutating = registry.filter_by_risk([RiskLevel.WRITE, RiskLevel.EXECUTE])
    assert set(mutating) == {write_tool, async_tool}


def test_schema_generation_for_models() -> None:
    registry = ToolRegistry()
    registry.register(MockReadFileTool())

    schemas = registry.get_schemas()
    assert len(schemas) == 1
    schema: dict[str, Any] = schemas[0]
    assert schema["name"] == "read_file"
    assert schema["description"] == "Read file contents from workspace."
    assert schema["risk_level"] == "read"
    assert "properties" in schema["parameters"]
    assert "path" in schema["parameters"]["properties"]
    assert "max_bytes" in schema["parameters"]["properties"]
