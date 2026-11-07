"""Deterministic FakeProvider for testing agent loops and provider abstractions offline."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from termicode.providers.base import (
    CompletionRequest,
    CompletionResponse,
    LLMProvider,
)


class FakeProvider(LLMProvider):
    """Deterministic, in-memory LLM provider for tests.

    Never interacts with external networks. Yields predetermined responses or
    raises preconfigured exceptions to verify error handling and tool loops.
    """

    def __init__(
        self,
        responses: Sequence[CompletionResponse | Exception] | None = None,
        default_response: CompletionResponse | None = None,
        handler: Callable[[CompletionRequest], CompletionResponse | Exception] | None = None,
    ) -> None:
        self._responses: list[CompletionResponse | Exception] = list(responses or [])
        self._default_response = default_response or CompletionResponse(
            content="Deterministic fake response.",
            finish_reason="stop",
            model="fake-model-v1",
        )
        self._handler = handler
        self.calls: list[CompletionRequest] = []

    @property
    def call_count(self) -> int:
        """Total number of complete/acomplete invocations received."""
        return len(self.calls)

    @property
    def last_request(self) -> CompletionRequest | None:
        """The most recent CompletionRequest received, if any."""
        return self.calls[-1] if self.calls else None

    def enqueue(self, *items: CompletionResponse | Exception) -> None:
        """Append responses or exceptions to the return queue."""
        self._responses.extend(items)

    def reset(self) -> None:
        """Clear recorded calls and queued responses."""
        self.calls.clear()
        self._responses.clear()

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Synchronously fulfill a completion request deterministically."""
        self.calls.append(request)

        if self._handler is not None:
            outcome = self._handler(request)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        if self._responses:
            next_item = self._responses.pop(0)
            if isinstance(next_item, Exception):
                raise next_item
            return next_item

        return self._default_response

    async def acomplete(self, request: CompletionRequest) -> CompletionResponse:
        """Asynchronously fulfill a completion request deterministically."""
        return self.complete(request)
