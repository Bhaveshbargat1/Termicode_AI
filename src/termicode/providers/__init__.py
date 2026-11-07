"""Provider abstraction and models for TermiCode AI."""

from termicode.providers.base import (
    ChatMessage,
    CompletionRequest,
    CompletionResponse,
    LLMProvider,
    MalformedResponseError,
    MessageRole,
    ProviderAuthError,
    ProviderConnectionError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ToolCall,
    ToolDeclaration,
)
from termicode.providers.fake import FakeProvider
from termicode.providers.openai import OpenAIProvider
from termicode.providers.redaction import redact_headers, redact_secrets

__all__ = [
    "ChatMessage",
    "CompletionRequest",
    "CompletionResponse",
    "FakeProvider",
    "LLMProvider",
    "MalformedResponseError",
    "MessageRole",
    "OpenAIProvider",
    "ProviderAuthError",
    "ProviderConnectionError",
    "ProviderError",
    "ProviderRateLimitError",
    "ProviderTimeoutError",
    "ToolCall",
    "ToolDeclaration",
    "redact_headers",
    "redact_secrets",
]
