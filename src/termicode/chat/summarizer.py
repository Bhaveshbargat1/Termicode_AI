"""Memory summarizer stub for condensing pruned conversation messages."""

from __future__ import annotations

from typing import Any

from termicode.providers.base import ChatMessage, CompletionRequest, LLMProvider, MessageRole
from termicode.providers.redaction import redact_secrets


class MemorySummarizerStub:
    """Condenses pruned older conversation messages into a persistent memory summary."""

    def __init__(self, provider: LLMProvider | None = None) -> None:
        self.provider = provider

    def summarize(
        self,
        messages_to_prune: list[dict[str, Any]],
        existing_summary: str | None = None,
    ) -> str:
        """Summarize conversation messages to preserve essential context before pruning.

        Args:
            messages_to_prune: Older messages being removed from active database window.
            existing_summary: Previously recorded summary text, if any.

        Returns:
            Redacted, updated condensed summary string.
        """
        if not messages_to_prune:
            return existing_summary or ""

        # Format conversation text
        dialogue_lines: list[str] = []
        for m in messages_to_prune:
            role = m.get("role", "unknown").capitalize()
            content = m.get("content", "").strip()
            preview = content.split("\n")[0][:100]
            dialogue_lines.append(f"{role}: {preview}")

        dialogue_text = "\n".join(dialogue_lines)

        prompt = (
            "Summarize the above conversation succinctly, preserving user intent, "
            "technical decisions, and code context:\n\n"
            f"{dialogue_text}"
        )

        summary_result: str = ""
        if self.provider is not None:
            try:
                req = CompletionRequest(
                    messages=[
                        ChatMessage(
                            role=MessageRole.SYSTEM,
                            content="You are a concise conversation memory summarizer.",
                        ),
                        ChatMessage(role=MessageRole.USER, content=prompt),
                    ],
                )
                resp = self.provider.complete(req)
                if resp.content and resp.content != "Deterministic fake response.":
                    summary_result = resp.content.strip()
            except Exception:
                summary_result = ""

        # Fallback structured stub when offline or provider unavailable
        if not summary_result:
            count = len(messages_to_prune)
            first_user = next(
                (m["content"] for m in messages_to_prune if m.get("role") == "user"), "Initial task"
            )
            first_topic = first_user.split("\n")[0][:60]
            summary_result = (
                f"Condensed memory of {count} earlier turn(s): Discussed '{first_topic}'."
            )

        # Merge with existing historical summary if present
        if existing_summary and existing_summary.strip():
            combined = f"{existing_summary.strip()}\n- {summary_result.strip()}"
        else:
            combined = summary_result.strip()

        return redact_secrets(combined)
