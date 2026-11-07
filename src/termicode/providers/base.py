"""Typed data models, exceptions, and protocol for LLM providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class MessageRole(StrEnum):
    """Role of a message within a conversation."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolCall(BaseModel):
    """Validated structured tool call proposed by an LLM.

    The model returns typed, structured arguments intended for tool schemas,
    never free-form unparsed shell commands.
    """

    id: str = Field(description="Unique identifier for the proposed tool invocation.")
    name: str = Field(description="Explicit name of the proposed tool.")
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured, validated parameter dictionary.",
    )
    rationale: str | None = Field(
        default=None,
        description="Optional model rationale or explanation for this proposal.",
    )


class ChatMessage(BaseModel):
    """A typed conversational message."""

    role: MessageRole = Field(description="Role of the message author.")
    content: str | None = Field(
        default=None,
        description="Text content of the message.",
    )
    tool_calls: list[ToolCall] = Field(
        default_factory=list,
        description="Structured tool proposals produced by assistant.",
    )
    tool_call_id: str | None = Field(
        default=None,
        description="Identifier linking a tool response message to its proposal.",
    )
    name: str | None = Field(
        default=None,
        description="Optional author name or tool identifier.",
    )


class ToolDeclaration(BaseModel):
    """Schema declaration for a tool exposed to an LLM provider."""

    name: str = Field(description="Name of the tool.")
    description: str = Field(description="Detailed explanation of tool purpose.")
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema dictionary specifying required and optional arguments.",
    )


class CompletionRequest(BaseModel):
    """Typed request payload sent to an LLM provider."""

    messages: list[ChatMessage] = Field(
        description="Chronological message history for the completion.",
    )
    tools: list[ToolDeclaration] = Field(
        default_factory=list,
        description="List of tool declarations the model may propose.",
    )
    temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=2.0,
        description="Sampling temperature.",
    )
    max_tokens: int | None = Field(
        default=None,
        ge=1,
        description="Upper limit on generated tokens.",
    )
    model: str | None = Field(
        default=None,
        description="Optional override for model identifier.",
    )


class CompletionResponse(BaseModel):
    """Typed response payload returned by an LLM provider."""

    content: str | None = Field(
        default=None,
        description="Text response from assistant, if any.",
    )
    tool_calls: list[ToolCall] = Field(
        default_factory=list,
        description="List of structured tool proposals.",
    )
    finish_reason: str = Field(
        default="stop",
        description="Termination reason (e.g., 'stop', 'tool_calls', 'length').",
    )
    model: str = Field(
        default="unknown",
        description="Specific model version that fulfilled the completion.",
    )
    usage: dict[str, int] = Field(
        default_factory=dict,
        description="Token consumption telemetry.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific telemetry and raw response metadata.",
    )


# --- Provider Exception Hierarchy ---


class ProviderError(Exception):
    """Base exception for all LLM provider communication and parsing errors."""


class ProviderAuthError(ProviderError):
    """Raised when authentication fails due to missing or invalid credentials."""


class ProviderTimeoutError(ProviderError):
    """Raised when an API request exceeds the configured network timeout."""


class ProviderConnectionError(ProviderError):
    """Raised on network connectivity, DNS, or server-side transport failures."""


class ProviderRateLimitError(ProviderError):
    """Raised when provider returns a rate limit (HTTP 429) status."""


class MalformedResponseError(ProviderError):
    """Raised when provider returns an unparseable or schema-violating payload."""


# --- Provider Interface ---


class LLMProvider(ABC):
    """Abstract interface defining the contract for LLM provider adapters."""

    @abstractmethod
    def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Synchronously complete a request against the provider."""
        raise NotImplementedError

    @abstractmethod
    async def acomplete(self, request: CompletionRequest) -> CompletionResponse:
        """Asynchronously complete a request against the provider."""
        raise NotImplementedError
