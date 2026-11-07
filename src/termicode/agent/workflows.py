"""Workflow profiles defining goals, tool permissions, and risk policies."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from termicode.tools.base import RiskLevel

READ_ONLY_TOOLS: frozenset[str] = frozenset({"list_files", "read_file", "search_text"})
ALL_STANDARD_TOOLS: frozenset[str] = frozenset(
    {"list_files", "read_file", "search_text", "write_file", "apply_patch", "run_command"}
)


class WorkflowType(StrEnum):
    """Supported workflow categories."""

    GENERATE = "generate"
    DEBUG = "debug"
    UNDERSTAND = "understand"
    TESTS = "tests"
    REVIEW = "review"
    REFACTOR = "refactor"


class WorkflowProfile(BaseModel):
    """Defines tool authorization rules and behavioral instructions for a workflow."""

    name: str = Field(description="Workflow identifier.")
    description: str = Field(description="Purpose of this workflow profile.")
    allowed_tools: set[str] = Field(
        description="Explicit allowlist of tool names accessible in this workflow.",
    )
    allowed_risk_levels: set[RiskLevel] = Field(
        description="Set of permitted tool risk levels (read/write/execute policy).",
    )
    system_instructions: str = Field(
        description="Core behavioral instructions injected into provider prompt.",
    )
    success_criteria: str = Field(
        description="Explicit criteria defining successful completion of the workflow.",
    )
    maximum_step_budget: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum step budget allocated to this workflow.",
    )

    def __init__(self, **data: Any) -> None:
        if "system_instructions" not in data and "system_prompt" in data:
            data["system_instructions"] = data["system_prompt"]
        if "maximum_step_budget" not in data and "max_step_budget" in data:
            data["maximum_step_budget"] = data["max_step_budget"]
        super().__init__(**data)

    @property
    def max_step_budget(self) -> int:
        """Alias for maximum_step_budget."""
        return self.maximum_step_budget

    @property
    def system_prompt(self) -> str:
        """Composite prompt combining behavioral instructions and success criteria."""
        return f"{self.system_instructions}\n\nSuccess Criteria:\n{self.success_criteria}"

    @property
    def allows_read(self) -> bool:
        """Check if read operations are permitted."""
        return RiskLevel.READ in self.allowed_risk_levels

    @property
    def allows_write(self) -> bool:
        """Check if write operations are permitted."""
        return RiskLevel.WRITE in self.allowed_risk_levels

    @property
    def allows_execute(self) -> bool:
        """Check if subprocess execution is permitted."""
        return RiskLevel.EXECUTE in self.allowed_risk_levels

    @property
    def is_read_only(self) -> bool:
        """Check if workflow is strictly read-only."""
        return self.allowed_risk_levels == {RiskLevel.READ}


_BASE_PROFILES: dict[str, WorkflowProfile] = {
    WorkflowType.GENERATE: WorkflowProfile(
        name=WorkflowType.GENERATE,
        description="Implement a small requested feature.",
        allowed_tools=set(ALL_STANDARD_TOOLS),
        allowed_risk_levels={RiskLevel.READ, RiskLevel.WRITE, RiskLevel.EXECUTE},
        system_instructions=(
            "You are TermiCode AI operating in GENERATE workflow mode. "
            "Your role is to implement small requested features cleanly and minimally. "
            "Inspect existing code first to understand architecture, types, and conventions. "
            "Propose targeted file writes or patches (which require user approval) and run "
            "allowlisted tests to verify your implementation. "
            "Keep diffs minimal and avoid unrelated changes."
        ),
        success_criteria=(
            "The requested feature is implemented cleanly with minimal diff, "
            "passes relevant tests, and introduces no regressions."
        ),
        maximum_step_budget=12,
    ),
    WorkflowType.DEBUG: WorkflowProfile(
        name=WorkflowType.DEBUG,
        description="Diagnose a failing test and propose a minimal fix.",
        allowed_tools=set(ALL_STANDARD_TOOLS),
        allowed_risk_levels={RiskLevel.READ, RiskLevel.WRITE, RiskLevel.EXECUTE},
        system_instructions=(
            "You are TermiCode AI operating in DEBUG workflow mode. "
            "Your role is to diagnose failing tests, inspect tracebacks and relevant source code, "
            "isolate the root cause, and propose a minimal targeted fix. "
            "Propose changes requiring user approval and verify the fix by re-running tests."
        ),
        success_criteria=(
            "The failing test passes, the root cause is clearly diagnosed, "
            "minimal code changes are made, and existing tests remain green."
        ),
        maximum_step_budget=10,
    ),
    WorkflowType.UNDERSTAND: WorkflowProfile(
        name=WorkflowType.UNDERSTAND,
        description="Read-only project explanation.",
        allowed_tools=set(READ_ONLY_TOOLS),
        allowed_risk_levels={RiskLevel.READ},
        system_instructions=(
            "You are TermiCode AI operating in UNDERSTAND workflow mode. "
            "Your role is strictly read-only: explore the workspace, inspect files, and provide "
            "clear architectural and behavioral explanations of the codebase. "
            "You cannot modify files or execute commands. Provide structured, concise answers."
        ),
        success_criteria=(
            "Accurate, clear, and comprehensive explanation of the project, architecture, "
            "or component with zero file modifications or command executions."
        ),
        maximum_step_budget=8,
    ),
    WorkflowType.TESTS: WorkflowProfile(
        name=WorkflowType.TESTS,
        description="Add or improve tests.",
        allowed_tools=set(ALL_STANDARD_TOOLS),
        allowed_risk_levels={RiskLevel.READ, RiskLevel.WRITE, RiskLevel.EXECUTE},
        system_instructions=(
            "You are TermiCode AI operating in TESTS workflow mode. "
            "Your role is to inspect implementation code and existing test suites, identify "
            "missing edge cases or coverage gaps, and write or improve targeted automated tests. "
            "Propose file modifications with user approval and execute tests to verify they pass."
        ),
        success_criteria=(
            "New or improved tests are created, cover targeted scenarios and edge cases, "
            "pass cleanly, and maintain test suite reliability."
        ),
        maximum_step_budget=10,
    ),
    WorkflowType.REVIEW: WorkflowProfile(
        name=WorkflowType.REVIEW,
        description="Read-only diff review.",
        allowed_tools=set(READ_ONLY_TOOLS),
        allowed_risk_levels={RiskLevel.READ},
        system_instructions=(
            "You are TermiCode AI operating in REVIEW workflow mode. "
            "Your role is strictly read-only: inspect code changes, diffs, and project structure "
            "to assess code quality, potential defects, security concerns, and maintainability. "
            "You cannot modify files or execute commands. Provide constructive review feedback."
        ),
        success_criteria=(
            "Thorough, prioritized review findings citing specific files and issues, "
            "with zero file modifications or command executions."
        ),
        maximum_step_budget=8,
    ),
    WorkflowType.REFACTOR: WorkflowProfile(
        name=WorkflowType.REFACTOR,
        description="Propose and apply a constrained refactor only after approval.",
        allowed_tools=set(ALL_STANDARD_TOOLS),
        allowed_risk_levels={RiskLevel.READ, RiskLevel.WRITE, RiskLevel.EXECUTE},
        system_instructions=(
            "You are TermiCode AI operating in REFACTOR workflow mode. "
            "Your goal is to propose and apply a constrained refactoring to improve structure, "
            "readability, or performance without changing external behavior. "
            "Always inspect code first, propose minimal refactorings requiring explicit user "
            "approval, and verify that all existing tests continue to pass."
        ),
        success_criteria=(
            "Targeted refactor improves code structure while preserving existing behavior, "
            "passes 100% of existing tests, and has explicit user approval."
        ),
        maximum_step_budget=10,
    ),
}


def get_workflow_profile(name: str | WorkflowType) -> WorkflowProfile:
    """Retrieve the WorkflowProfile matching the requested name."""
    clean_name = str(name).strip().lower()
    if clean_name not in _BASE_PROFILES:
        valid_names = sorted(_BASE_PROFILES.keys())
        raise ValueError(f"Unknown workflow profile '{name}'. Available workflows: {valid_names}")
    return _BASE_PROFILES[clean_name]


def list_workflows() -> list[str]:
    """Return sorted names of all available workflow profiles."""
    return sorted(_BASE_PROFILES.keys())
