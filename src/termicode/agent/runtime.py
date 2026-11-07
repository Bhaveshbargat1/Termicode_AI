"""Bounded agent loop runtime executing the propose-validate-approve-execute cycle."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from termicode.agent.memory import (
    ApprovedActionRecord,
    CommandExecutionRecord,
    LocalMemory,
    RunSummary,
)
from termicode.agent.models import (
    AgentRequest,
    AgentState,
    PolicyDecision,
    ProposedAction,
    RunResult,
    StopReason,
)
from termicode.agent.policy import PolicyEngine
from termicode.agent.transcript import TranscriptWriter
from termicode.agent.workflows import get_workflow_profile
from termicode.providers.base import (
    ChatMessage,
    CompletionRequest,
    LLMProvider,
    MessageRole,
    ProviderAuthError,
    ProviderError,
    ToolDeclaration,
)
from termicode.providers.redaction import redact_secrets
from termicode.tools.base import ToolContext
from termicode.tools.registry import ToolRegistry

MAX_TOOL_OUTPUT_CHARS = 16384


class AgentRuntime:
    """The central TermiCode single-agent bounded loop runtime.

    Orchestrates the deterministic propose -> validate -> approve -> execute cycle.
    The model proposes actions, but deterministic policy and approval gates control execution.
    """

    def __init__(
        self,
        provider: LLMProvider,
        registry: ToolRegistry,
        policy_engine: PolicyEngine | None = None,
        approval_handler: Callable[[ProposedAction, PolicyDecision], bool] | None = None,
        step_callback: Callable[[int, int, str, dict[str, Any]], None] | None = None,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._policy_engine = policy_engine or PolicyEngine(registry)
        # Default approval handler: denies execution unless explicitly authorized
        self._approval_handler = approval_handler or (lambda _act, _dec: False)
        self._step_callback = step_callback

    def run(self, request: AgentRequest) -> RunResult:
        """Synchronously execute the bounded agent loop."""
        run_id = uuid.uuid4().hex[:12]
        profile = get_workflow_profile(request.workflow)
        max_steps = (
            request.max_steps if request.max_steps is not None else profile.maximum_step_budget
        )
        transcript = TranscriptWriter(request.workspace, run_id=run_id)
        memory = LocalMemory(request.workspace)

        approved_actions: list[ApprovedActionRecord] = []
        command_records: list[CommandExecutionRecord] = []
        unresolved_issues: list[str] = []

        transcript.log_event(
            "run_start",
            0,
            {
                "task": request.task,
                "workflow": profile.name,
                "max_steps": max_steps,
                "max_repairs": request.max_repairs,
            },
        )

        state = AgentState(
            messages=[
                ChatMessage(role=MessageRole.SYSTEM, content=profile.system_prompt),
                ChatMessage(role=MessageRole.USER, content=request.task),
            ]
        )

        # Prepare tool declarations matching the active workflow profile
        allowed_tools = self._registry.filter_by_risk(profile.allowed_risk_levels)
        if profile.allowed_tools is not None:
            allowed_tools = [t for t in allowed_tools if t.name in profile.allowed_tools]

        tool_declarations = [
            ToolDeclaration(
                name=t.name,
                description=t.description,
                parameters=t.args_schema.model_json_schema(),
            )
            for t in allowed_tools
        ]

        # The bounded agent loop
        while state.steps_used < max_steps:
            # 1. Enforce repair budget
            if state.repairs_used >= request.max_repairs:
                state.stop_reason = StopReason.MAX_REPAIRS_REACHED
                break

            # 2. Ask provider for one structured next action
            comp_request = CompletionRequest(
                messages=state.messages,
                tools=tool_declarations,
                model=request.model,
            )

            try:
                response = self._provider.complete(comp_request)
            except ProviderAuthError as err:
                clean_err = redact_secrets(str(err))
                state.stop_reason = StopReason.ERROR
                unresolved_issues.append(f"Fatal provider error: {clean_err}")
                transcript.log_event("fatal_provider_error", state.steps_used, {"error": clean_err})
                state.messages.append(
                    ChatMessage(
                        role=MessageRole.ASSISTANT, content=f"Fatal provider error: {clean_err}"
                    )
                )
                break
            except ProviderError as err:
                clean_err = redact_secrets(str(err))
                state.repairs_used += 1
                transcript.log_event(
                    "recoverable_provider_error",
                    state.steps_used,
                    {"error": clean_err},
                )
                if state.repairs_used >= request.max_repairs:
                    state.stop_reason = StopReason.MAX_REPAIRS_REACHED
                    unresolved_issues.append(f"Provider error: {clean_err}")
                    break
                state.messages.append(
                    ChatMessage(
                        role=MessageRole.USER,
                        content=f"Provider completion error: {clean_err}. Please retry.",
                    )
                )
                continue

            # 3. Model completed without tool calls
            if not response.tool_calls:
                state.stop_reason = StopReason.COMPLETED
                final_content = response.content or "(Task finished without textual explanation.)"
                state.messages.append(
                    ChatMessage(role=MessageRole.ASSISTANT, content=final_content)
                )
                transcript.log_event(
                    "completion_response", state.steps_used, {"content": final_content}
                )
                break

            # 4. Extract single next tool proposal
            tc = response.tool_calls[0]
            state.steps_used += 1

            if self._step_callback is not None:
                self._step_callback(state.steps_used, max_steps, tc.name, tc.arguments)

            action = ProposedAction(
                kind="tool_call",
                tool_name=tc.name,
                arguments=tc.arguments,
                rationale=tc.rationale,
            )
            transcript.log_event(
                "proposed_action",
                state.steps_used,
                {"id": tc.id, "tool": tc.name, "arguments": tc.arguments},
            )

            # 5. Policy evaluation
            decision = self._policy_engine.evaluate(action, profile, request.workspace)
            transcript.log_event(
                "policy_evaluation",
                state.steps_used,
                {"status": decision.status, "reason": decision.reason},
            )

            if decision.status == "deny":
                state.stop_reason = StopReason.POLICY_REJECTED
                unresolved_issues.append(f"Policy denied '{tc.name}': {decision.reason}")
                state.messages.append(ChatMessage(role=MessageRole.ASSISTANT, tool_calls=[tc]))
                state.messages.append(
                    ChatMessage(
                        role=MessageRole.TOOL,
                        tool_call_id=tc.id,
                        name=tc.name,
                        content=f"Policy violation: {decision.reason}",
                    )
                )
                break

            if decision.status in ("unknown_tool", "invalid_arguments"):
                state.repairs_used += 1
                transcript.log_event(
                    "repair_needed",
                    state.steps_used,
                    {"reason": decision.reason, "repairs_used": state.repairs_used},
                )
                state.messages.append(ChatMessage(role=MessageRole.ASSISTANT, tool_calls=[tc]))
                state.messages.append(
                    ChatMessage(
                        role=MessageRole.TOOL,
                        tool_call_id=tc.id,
                        name=tc.name,
                        content=f"Validation error: {decision.reason}",
                    )
                )
                if state.repairs_used >= request.max_repairs:
                    state.stop_reason = StopReason.MAX_REPAIRS_REACHED
                    unresolved_issues.append(
                        f"Validation failure on '{tc.name}': {decision.reason}"
                    )
                    break
                continue

            # 6. User approval determination
            user_approved = True
            if decision.status == "needs_approval":
                transcript.log_event(
                    "approval_requested",
                    state.steps_used,
                    {"tool": tc.name, "preview": decision.preview},
                )
                user_approved = self._approval_handler(action, decision)
                transcript.log_event(
                    "approval_determination",
                    state.steps_used,
                    {"approved": user_approved},
                )
                if user_approved:
                    approved_actions.append(
                        ApprovedActionRecord(
                            tool_name=tc.name,
                            arguments=tc.arguments,
                            preview=decision.preview,
                            step=state.steps_used,
                        )
                    )
                else:
                    state.stop_reason = StopReason.USER_CANCELLED
                    unresolved_issues.append(f"User cancelled action '{tc.name}'.")
                    state.messages.append(ChatMessage(role=MessageRole.ASSISTANT, tool_calls=[tc]))
                    state.messages.append(
                        ChatMessage(
                            role=MessageRole.TOOL,
                            tool_call_id=tc.id,
                            name=tc.name,
                            content="User cancelled execution: Action was denied by approval gate.",
                        )
                    )
                    break

            # 7. Execute tool proposal
            tool_ctx = ToolContext(
                workspace=request.workspace,
                step_index=state.steps_used,
                run_id=run_id,
                user_confirmed=user_approved,
            )

            tool_result = self._registry.execute(
                tc.name,
                tc.arguments,
                context=tool_ctx,
            )

            transcript.log_event(
                "tool_execution",
                state.steps_used,
                {
                    "ok": tool_result.ok,
                    "changed_files": tool_result.changed_files,
                    "error": tool_result.error,
                },
            )

            # Record telemetry and state changes
            if tool_result.ok:
                state.repairs_used = 0  # Success resets consecutive repair counter
                for filepath in tool_result.changed_files:
                    if filepath not in state.changed_files:
                        state.changed_files.append(filepath)

                if tc.name == "run_command":
                    cmd_val = tc.arguments.get("command")
                    cmd_str = " ".join(cmd_val) if isinstance(cmd_val, list) else str(cmd_val)
                    state.commands_run.append(cmd_str)
                    is_test = "pytest" in cmd_str
                    if is_test:
                        state.tests_run.append(cmd_str)

                    exit_code = tool_result.metadata.get("exit_code") if tool_result.metadata else 0
                    duration_ms = (
                        tool_result.metadata.get("duration_ms") if tool_result.metadata else None
                    )
                    duration_sec = duration_ms / 1000.0 if duration_ms is not None else None
                    command_records.append(
                        CommandExecutionRecord(
                            command=cmd_str,
                            ok=True,
                            exit_code=exit_code,
                            output=tool_result.output[:1024] if tool_result.output else None,
                            duration_seconds=duration_sec,
                            is_test=is_test,
                        )
                    )
            else:
                state.repairs_used += 1
                if tc.name == "run_command":
                    cmd_val = tc.arguments.get("command")
                    cmd_str = " ".join(cmd_val) if isinstance(cmd_val, list) else str(cmd_val)
                    exit_code = tool_result.metadata.get("exit_code") if tool_result.metadata else 1
                    is_test = "pytest" in cmd_str
                    command_records.append(
                        CommandExecutionRecord(
                            command=cmd_str,
                            ok=False,
                            exit_code=exit_code,
                            output=tool_result.error or tool_result.output[:1024],
                            is_test=is_test,
                        )
                    )
                    unresolved_issues.append(
                        f"Command '{cmd_str}' failed: {tool_result.error or 'Non-zero exit'}"
                    )
                else:
                    unresolved_issues.append(
                        f"Tool '{tc.name}' failed: {tool_result.error or 'Execution failed'}"
                    )

                if state.repairs_used >= request.max_repairs:
                    state.stop_reason = StopReason.MAX_REPAIRS_REACHED
                    output_err = tool_result.error or "Tool execution failed."
                    state.messages.append(ChatMessage(role=MessageRole.ASSISTANT, tool_calls=[tc]))
                    state.messages.append(
                        ChatMessage(
                            role=MessageRole.TOOL,
                            tool_call_id=tc.id,
                            name=tc.name,
                            content=output_err,
                        )
                    )
                    break

            # Bound output content length
            raw_output = (
                tool_result.output if tool_result.ok else (tool_result.error or "Tool failed.")
            )
            if len(raw_output) > MAX_TOOL_OUTPUT_CHARS:
                output_content = (
                    raw_output[:MAX_TOOL_OUTPUT_CHARS]
                    + f"\n\n[Warning: Output truncated at {MAX_TOOL_OUTPUT_CHARS:,} chars]"
                )
            else:
                output_content = raw_output

            # Append to message history
            state.messages.append(ChatMessage(role=MessageRole.ASSISTANT, tool_calls=[tc]))
            state.messages.append(
                ChatMessage(
                    role=MessageRole.TOOL,
                    tool_call_id=tc.id,
                    name=tc.name,
                    content=output_content,
                )
            )

        # 8. Post-loop finalize
        if state.stop_reason is None:
            state.stop_reason = StopReason.MAX_STEPS_REACHED

        if state.stop_reason == StopReason.MAX_STEPS_REACHED:
            unresolved_issues.append(
                f"Maximum step budget ({max_steps}) reached before task completion."
            )
        elif state.stop_reason == StopReason.MAX_REPAIRS_REACHED:
            unresolved_issues.append(
                f"Maximum consecutive repair attempts ({request.max_repairs}) reached."
            )
        elif state.stop_reason == StopReason.ERROR:
            unresolved_issues.append("Run terminated due to an unrecoverable error.")

        # Determine summary text
        summary = ""
        for msg in reversed(state.messages):
            if msg.role == MessageRole.ASSISTANT and msg.content:
                summary = msg.content
                break

        if not summary:
            summary = (
                f"Run stopped with status '{state.stop_reason.value}' after "
                f"{state.steps_used} steps and {state.repairs_used} repairs."
            )

        transcript.log_event(
            "run_stop",
            state.steps_used,
            {
                "stop_reason": state.stop_reason.value,
                "summary": summary,
                "changed_files": state.changed_files,
                "commands_run": state.commands_run,
            },
        )

        run_summary = RunSummary(
            run_id=run_id,
            timestamp=datetime.now(UTC).isoformat(),
            task_objective=request.task,
            workflow=profile.name,
            final_stop_reason=state.stop_reason,
            approved_actions=approved_actions,
            changed_files=state.changed_files,
            commands_and_tests=command_records,
            unresolved_issues=unresolved_issues,
            summary=summary,
            steps_used=state.steps_used,
            repairs_used=state.repairs_used,
            transcript_path=str(transcript.path),
        )

        memory.save_summary(run_summary)

        return RunResult(
            summary=summary,
            stop_reason=state.stop_reason,
            steps_used=state.steps_used,
            repairs_used=state.repairs_used,
            changed_files=state.changed_files,
            commands_run=state.commands_run,
            tests_run=state.tests_run,
            transcript_path=transcript.path,
            run_summary=run_summary,
        )

    async def arun(self, request: AgentRequest) -> RunResult:
        """Asynchronously execute the bounded agent loop in a worker thread."""
        return await asyncio.to_thread(self.run, request)
