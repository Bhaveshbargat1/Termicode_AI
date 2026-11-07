"""Deterministic policy engine evaluating proposed agent actions."""

from __future__ import annotations

import difflib

from termicode.agent.models import PolicyDecision, ProposedAction
from termicode.agent.workflows import WorkflowProfile
from termicode.tools.base import RiskLevel, ToolArgumentError
from termicode.tools.registry import ToolRegistry
from termicode.workspace import WorkspaceBoundary


class PolicyEngine:
    """Evaluates proposed actions against active workflow boundaries and risk profiles."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def _extract_approval_details(
        self, action: ProposedAction, boundary: WorkspaceBoundary
    ) -> tuple[str | None, str | None, list[str] | None, str | None]:
        """Construct preview, target_path, command_args, and diff for actions requiring approval."""
        if not action.tool_name:
            return None, None, None, None

        if action.tool_name == "write_file":
            path_str = str(action.arguments.get("path", ""))
            content = str(action.arguments.get("content", ""))
            try:
                resolved = boundary.resolve_safe(path_str)
                rel_path = resolved.relative_to(boundary.root).as_posix()
                old_content = resolved.read_text(encoding="utf-8") if resolved.is_file() else ""
            except Exception:
                old_content = ""
                rel_path = path_str

            diff_lines = list(
                difflib.unified_diff(
                    old_content.splitlines(keepends=True),
                    content.splitlines(keepends=True),
                    fromfile=f"a/{rel_path}" if old_content else "/dev/null",
                    tofile=f"b/{rel_path}",
                    lineterm="",
                )
            )
            diff_text = "\n".join(diff_lines) if diff_lines else "(No content changes)"
            return diff_text, rel_path, None, diff_text

        elif action.tool_name == "apply_patch":
            path_str = str(action.arguments.get("path", ""))
            patch = str(action.arguments.get("patch", ""))
            try:
                resolved = boundary.resolve_safe(path_str)
                rel_path = resolved.relative_to(boundary.root).as_posix()
            except Exception:
                rel_path = path_str
            preview = f"Patch for {rel_path}:\n{patch.strip()}"
            return preview, rel_path, None, patch.strip()

        elif action.tool_name == "run_command":
            cmd = action.arguments.get("command", [])
            cmd_args = cmd if isinstance(cmd, list) else [str(cmd)]
            cmd_str = " ".join(cmd_args)
            preview = f"Execute command in {boundary.root}:\n  {cmd_str}"
            return preview, None, cmd_args, None

        return None, None, None, None

    def _generate_preview(self, action: ProposedAction, boundary: WorkspaceBoundary) -> str | None:
        """Construct a human-readable preview for actions requiring approval."""
        return self._extract_approval_details(action, boundary)[0]

    def evaluate(
        self,
        action: ProposedAction,
        workflow: WorkflowProfile,
        boundary: WorkspaceBoundary,
    ) -> PolicyDecision:
        """Determine whether an action is permitted, denied, or requires approval."""
        if action.kind == "final":
            return PolicyDecision(
                status="permit",
                reason="Final textual answer permitted.",
            )

        if action.kind != "tool_call":
            return PolicyDecision(
                status="deny",
                reason=f"Unsupported action kind '{action.kind}'.",
            )

        if not action.tool_name:
            return PolicyDecision(
                status="deny",
                reason="Tool call missing tool name.",
            )

        # 1. Check registry existence
        if not self._registry.has(action.tool_name):
            return PolicyDecision(
                status="unknown_tool",
                reason=f"Tool '{action.tool_name}' is not registered in the tool registry.",
            )

        tool = self._registry.get(action.tool_name)

        # 2. Check workflow risk-level permission
        if tool.risk_level not in workflow.allowed_risk_levels:
            allowed_str = [r.value for r in workflow.allowed_risk_levels]
            return PolicyDecision(
                status="deny",
                reason=(
                    f"Tool '{tool.name}' with risk '{tool.risk_level.value}' is prohibited "
                    f"in workflow '{workflow.name}'. Allowed risks: {allowed_str}"
                ),
                risk_level=tool.risk_level,
            )

        # 3. Check workflow explicit tool allowlist if specified
        if workflow.allowed_tools is not None and tool.name not in workflow.allowed_tools:
            return PolicyDecision(
                status="deny",
                reason=(
                    f"Tool '{tool.name}' is not in the allowlist for workflow '{workflow.name}'."
                ),
                risk_level=tool.risk_level,
            )

        # 4. Check schema validation for arguments
        try:
            tool.validate_args(action.arguments)
        except ToolArgumentError as err:
            return PolicyDecision(
                status="invalid_arguments",
                reason=f"Arguments failed schema validation: {err}",
                risk_level=tool.risk_level,
            )

        # 5. Check approval requirement (writes and command executions require approval)
        if tool.risk_level in (RiskLevel.WRITE, RiskLevel.EXECUTE):
            preview, target_path, command_args, diff = self._extract_approval_details(
                action, boundary
            )
            return PolicyDecision(
                status="needs_approval",
                reason=(
                    f"Tool '{tool.name}' ({tool.risk_level.value}) "
                    "requires explicit user confirmation."
                ),
                risk_level=tool.risk_level,
                preview=preview,
                target_path=target_path,
                command_args=command_args,
                diff=diff,
            )

        # 6. Read operations permitted
        return PolicyDecision(
            status="permit",
            reason=f"Read-only tool '{tool.name}' permitted.",
            risk_level=tool.risk_level,
        )
