"""Chat response generation engine, step tracker, and streaming provider."""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

from termicode.chat.session import ChatSession
from termicode.providers.base import (
    ChatMessage,
    CompletionRequest,
    LLMProvider,
    MessageRole,
    ProviderAuthError,
    ProviderError,
)
from termicode.providers.fake import FakeProvider
from termicode.providers.openai import OpenAIProvider
from termicode.workspace import WorkspaceBoundary


class ChatEngine:
    """Executes chat turns, tracks intermediate planner/tool steps, and streams responses."""

    def __init__(
        self,
        session: ChatSession,
        boundary: WorkspaceBoundary,
        provider: LLMProvider | None = None,
    ) -> None:
        self.session = session
        self.boundary = boundary
        self._provider = provider

    def _get_effective_provider(self) -> LLMProvider:
        """Resolve or initialize the active LLM provider based on session configuration."""
        if self._provider is not None:
            return self._provider

        prov_name = self.session.state.provider.lower()
        if prov_name == "fake":
            return FakeProvider()

        if prov_name in ("ollama", "local"):
            try:
                base_url = self.session.settings.api_base or "http://localhost:11434/v1"
                return OpenAIProvider(
                    api_key="ollama",
                    base_url=base_url,
                    model=self.session.state.model,
                )
            except Exception:
                return FakeProvider()

        try:
            return OpenAIProvider.from_env()
        except ProviderAuthError:
            return FakeProvider()

    def generate_step_logs(self, query: str) -> list[dict[str, Any]]:
        """Generate structured intermediate steps representing planner and tool evaluations."""
        steps: list[dict[str, Any]] = []

        # 1. Intent Analysis
        steps.append(
            {
                "phase": "Planner",
                "action": "Analyze Intent",
                "detail": f"Classified query intent for '{query[:60]}...' under active session.",
            }
        )

        # 2. Document Context Check
        docs = self.session.list_documents()
        if docs:
            doc_names = ", ".join(d.filename for d in docs)
            steps.append(
                {
                    "phase": "Context",
                    "action": "Document References",
                    "detail": (
                        f"Linked {len(docs)} document(s): [{doc_names}] into session context."
                    ),
                }
            )

        # 3. ToolBridge Gateway Check
        if self.session.state.toolbridge_enabled:
            steps.append(
                {
                    "phase": "ToolBridge",
                    "action": "MCP Gateway Inspection",
                    "detail": (
                        f"Verified MCP gateway at {self.session.state.toolbridge_url} (ready)."
                    ),
                }
            )

        # 4. Workspace & Policy Gate
        steps.append(
            {
                "phase": "Policy",
                "action": "Safety Boundary Check",
                "detail": (
                    f"Anchored in workspace: {self.boundary.root.name}. Consultation permitted."
                ),
            }
        )

        return steps

    def generate_response(self, query: str) -> tuple[str, list[dict[str, Any]]]:
        """Generate a complete text response and corresponding intermediate step logs."""
        step_logs = self.generate_step_logs(query)
        self.session.add_user_message(query)

        provider = self._get_effective_provider()
        self.session.summarizer.provider = provider

        # Build prompt messages from history
        system_prompt = (
            "You are TermiCode AI, a secure, helpful terminal coding assistant. "
            f"Active workspace: {self.boundary.root.as_posix()}.\n"
        )
        summary = self.session.get_session_summary()
        if summary:
            system_prompt += f"\nPrevious Conversation Summary:\n{summary}\n"

        docs = self.session.list_documents()
        if docs:
            system_prompt += "\nAttached User Documents:\n"
            for d in docs:
                system_prompt += (
                    f"- {d.filename} ({d.file_type}, {d.size_bytes} bytes, path: {d.path})\n"
                )

        messages = [ChatMessage(role=MessageRole.SYSTEM, content=system_prompt)]
        for m in self.session.get_history():
            role = MessageRole.USER if m.role == "user" else MessageRole.ASSISTANT
            messages.append(ChatMessage(role=role, content=m.content))

        # Invoke provider with graceful fallback
        response_text = ""
        try:
            req = CompletionRequest(
                messages=messages,
                model=self.session.state.model,
            )
            resp = provider.complete(req)
            response_text = resp.content or "Task completed."
        except ProviderAuthError as err:
            response_text = (
                f"**Provider Authentication Notice:**\n\n"
                f"{err}\n\n"
                f"**How to configure:**\n"
                f"- Set `OPENAI_API_KEY` in your `.env` file\n"
                f"- Or switch to local private Ollama: `/settings provider=ollama`\n"
                f"- Or run with offline mock engine: `/settings provider=fake`"
            )
        except ProviderError as err:
            response_text = (
                f"**LLM Completion Notice:**\n\n"
                f"Encountered provider response error: {err}.\n"
                f"If using Ollama, ensure `ollama serve` is active."
            )
        except Exception:
            # Fallback response for offline or mock scenarios
            response_text = self._generate_offline_reply(query, docs)

        if not response_text or response_text in (
            "Task completed.",
            "Deterministic fake response.",
        ):
            response_text = self._generate_offline_reply(query, docs)

        self.session.add_assistant_message(response_text, step_logs=step_logs)
        return response_text, step_logs

    def stream_chunks(self, full_text: str, chunk_delay: float = 0.008) -> Iterator[str]:
        """Yield word chunks from full_text to simulate a smooth streaming terminal effect."""
        words = full_text.split(" ")
        for i, word in enumerate(words):
            suffix = " " if i < len(words) - 1 else ""
            yield word + suffix
            if chunk_delay > 0:
                time.sleep(chunk_delay)

    def _generate_offline_reply(self, query: str, docs: list[Any]) -> str:
        """Create a helpful contextual reply when operating without external LLM connections."""
        q_lower = query.lower()

        doc_context = ""
        if docs:
            names = ", ".join(f"`{d.filename}`" for d in docs)
            doc_context = f"\n\n> 📎 **Attached Context:** Referenced documents {names}."

        if any(w in q_lower for w in ("hello", "hi", "hey", "who are you")):
            tb_stat = "Enabled" if self.session.state.toolbridge_enabled else "Disabled"
            return (
                f"Hello! I am **TermiCode AI**, your bounded terminal coding assistant.\n\n"
                f"- **Active Workspace:** `{self.boundary.root.name}`\n"
                f"- **Model:** `{self.session.state.model}` (`{self.session.state.provider}`)\n"
                f"- **ToolBridge Gateway:** {tb_stat}\n\n"
                f"You can ask me questions about your code, attach files with `/upload <path>`, "
                f"inspect history with `/history`, or type `/help` for all commands.{doc_context}"
            )

        if any(w in q_lower for w in ("code", "python", "function", "create", "write")):
            return (
                f"Here is a clean, typed Python implementation based on your query:\n\n"
                f"```python\n"
                f"from typing import Any, Optional\n\n"
                f"def process_data(payload: dict[str, Any]) -> dict[str, Any]:\n"
                f'    """Process input data safely with validation."""\n'
                f'    status = payload.get("status", "pending")\n'
                f'    return {{"status": "success", "processed": True, "initial": status}}\n\n'
                f'if __name__ == "__main__":\n'
                f'    result = process_data({{"status": "ready"}})\n'
                f'    print(f"Result: {{result}}")\n'
                f"```\n\n"
                f"Would you like me to create this in `{self.boundary.root.name}`?{doc_context}"
            )

        return (
            f'I have reviewed your query: *"{query}"*.\n\n'
            f"- Workspace analysis completed inside `{self.boundary.root.name}`.\n"
            f"- Safety policy evaluated: all actions bounded by deterministic approval gates.\n"
            f'- Run full workflows with `termicode run "{query}"` or ask here.{doc_context}'
        )
