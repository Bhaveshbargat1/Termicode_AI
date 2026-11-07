"""Typed domain models for the TermiCode AI agent runtime, state, and policy decisions."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from termicode.providers.base import ChatMessage
from termicode.tools.base import RiskLevel
from termicode.workspace import WorkspaceBoundary


class StopReason(StrEnum):
    """Explicit deterministic reason the agent loop terminated."""

    COMPLETED = "completed"
    MAX_STEPS_REACHED = "max_steps_reached"
    MAX_REPAIRS_REACHED = "max_repairs_reached"
    USER_CANCELLED = "user_cancelled"
    POLICY_REJECTED = "policy_rejected"
    ERROR = "error"


class ProposedAction(BaseModel):
    """Normalized proposal produced by the provider before policy validation."""

    kind: str = Field(
        description="'final' when answering directly, or 'tool_call' when requesting action.",
    )
    tool_name: str | None = Field(
        default=None,
        description="Name of the requested tool if kind == 'tool_call'.",
    )
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured parameter dictionary for the tool.",
    )
    rationale: str | None = Field(
        default=None,
        description="Optional model reasoning or chain-of-thought.",
    )


class PolicyDecision(BaseModel):
    """Deterministic evaluation of a proposed action by the policy engine."""

    status: str = Field(
        description="'permit', 'deny', or 'needs_approval'.",
    )
    reason: str = Field(
        description="Human-readable explanation of the policy determination.",
    )
    risk_level: RiskLevel | None = Field(
        default=None,
        description="Evaluated risk level of the target action.",
    )
    preview: str | None = Field(
        default=None,
        description="Text or diff preview to present for user confirmation.",
    )
    target_path: str | None = Field(
        default=None,
        description="Workspace-relative file path targeted by the action.",
    )
    command_args: list[str] | None = Field(
        default=None,
        description="Structured command arguments if the action is command execution.",
    )
    diff: str | None = Field(
        default=None,
        description="Unified diff preview if the action modifies a file.",
    )


class AgentRequest(BaseModel):
    """Input configuration request initiating an agent run."""

    task: str = Field(description="The user's goal or coding instruction.")
    workspace: WorkspaceBoundary = Field(
        description="Strictly resolved workspace boundary.",
    )
    workflow: str = Field(
        default="understand",
        description="Workflow profile name (e.g. 'understand', 'debug', 'generate').",
    )
    max_steps: int | None = Field(
        default=None,
        ge=1,
        le=50,
        description="Maximum allowed tool execution steps. Defaults to workflow budget.",
    )
    max_repairs: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Maximum consecutive repair iterations permitted upon failure.",
    )
    model: str | None = Field(
        default=None,
        description="Optional override for target model.",
    )

    model_config = ConfigDict(arbitrary_types_allowed=True)


class AgentState(BaseModel):
    """Mutable runtime state tracked across the agent loop."""

    messages: list[ChatMessage] = Field(
        default_factory=list,
        description="Full conversation history sent to provider.",
    )
    steps_used: int = Field(
        default=0,
        description="Count of tool execution proposals dispatched.",
    )
    repairs_used: int = Field(
        default=0,
        description="Count of consecutive tool or validation failures.",
    )
    changed_files: list[str] = Field(
        default_factory=list,
        description="Unique files modified during the run.",
    )
    commands_run: list[str] = Field(
        default_factory=list,
        description="Subprocess commands executed during the run.",
    )
    tests_run: list[str] = Field(
        default_factory=list,
        description="Testing commands executed during the run.",
    )
    pending_approval: ProposedAction | None = Field(
        default=None,
        description="Action currently awaiting user confirmation.",
    )
    stop_reason: StopReason | None = Field(
        default=None,
        description="Final termination reason once loop ends.",
    )


class ApprovedActionRecord(BaseModel):
    """Record of a mutating action approved by user and executed."""

    tool_name: str = Field(description="Name of the approved tool.")
    arguments: dict[str, Any] = Field(default_factory=dict, description="Tool arguments.")
    preview: str | None = Field(default=None, description="Preview presented to user.")
    step: int = Field(default=0, description="Step index when action was executed.")


class CommandExecutionRecord(BaseModel):
    """Record of an allowlisted command or test executed during a run."""

    command: str = Field(description="Command string executed.")
    ok: bool = Field(description="Whether execution succeeded (exit code 0 and no timeout).")
    exit_code: int | None = Field(default=None, description="Subprocess exit code.")
    output: str | None = Field(default=None, description="Truncated command output.")
    duration_seconds: float | None = Field(
        default=None, description="Execution duration in seconds."
    )
    is_test: bool = Field(default=False, description="Whether command was a test suite execution.")


class RunSummary(BaseModel):
    """Compact structured summary of an agent run stored in .termicode/summaries/."""

    run_id: str = Field(description="Unique identifier for the run.")
    timestamp: str = Field(description="UTC timestamp when run was concluded.")
    task_objective: str = Field(description="Original user task objective or instruction.")
    workflow: str = Field(default="understand", description="Workflow profile used.")
    final_stop_reason: StopReason = Field(description="Final deterministic stop reason.")
    approved_actions: list[ApprovedActionRecord] = Field(
        default_factory=list,
        description="All mutating tool actions explicitly approved and executed.",
    )
    changed_files: list[str] = Field(
        default_factory=list,
        description="Workspace files modified or created during the run.",
    )
    commands_and_tests: list[CommandExecutionRecord] = Field(
        default_factory=list,
        description="Commands and tests executed along with their outcomes.",
    )
    unresolved_issues: list[str] = Field(
        default_factory=list,
        description="Errors, failed repairs, or remaining unaddressed problems.",
    )
    summary: str = Field(
        default="",
        description="Assistant's final textual response or explanation.",
    )
    steps_used: int = Field(default=0, description="Total tool steps used.")
    repairs_used: int = Field(default=0, description="Consecutive repairs at termination.")
    transcript_path: str | None = Field(
        default=None,
        description="Path to the JSONL transcript file.",
    )

    @property
    def stop_reason(self) -> StopReason:
        """Alias for final_stop_reason."""
        return self.final_stop_reason


class RunResult(BaseModel):
    """Final auditable result returned when an agent run finishes."""

    summary: str = Field(
        description="Final assistant summary, response, or failure diagnosis.",
    )
    stop_reason: StopReason = Field(
        description="Explicit reason the run concluded.",
    )
    steps_used: int = Field(
        description="Total tool execution steps consumed.",
    )
    repairs_used: int = Field(
        description="Final consecutive repair count.",
    )
    changed_files: list[str] = Field(
        default_factory=list,
        description="Workspace files modified or created during execution.",
    )
    commands_run: list[str] = Field(
        default_factory=list,
        description="All executed subprocess commands.",
    )
    tests_run: list[str] = Field(
        default_factory=list,
        description="Test execution commands dispatched.",
    )
    transcript_path: Path | None = Field(
        default=None,
        description="Path to local .termicode JSONL transcript file.",
    )
    run_summary: RunSummary | None = Field(
        default=None,
        description="Compact task summary stored in local memory.",
    )

    @property
    def run_id(self) -> str | None:
        """Convenience property accessing run_id from run_summary."""
        return self.run_summary.run_id if self.run_summary else None
