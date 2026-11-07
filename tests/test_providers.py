"""Unit tests for provider abstraction, FakeProvider, OpenAI adapter, and redaction."""

from __future__ import annotations

import asyncio
import json
import urllib.error
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from termicode.providers import (
    ChatMessage,
    CompletionRequest,
    CompletionResponse,
    FakeProvider,
    MalformedResponseError,
    MessageRole,
    OpenAIProvider,
    ProviderAuthError,
    ProviderConnectionError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ToolCall,
    ToolDeclaration,
    redact_headers,
    redact_secrets,
)

# --- FakeProvider Tests ---


def test_fake_provider_text_completion() -> None:
    fake = FakeProvider(
        responses=[
            CompletionResponse(
                content="Hello from fake model",
                finish_reason="stop",
                model="fake-v1",
            )
        ]
    )

    req = CompletionRequest(
        messages=[ChatMessage(role=MessageRole.USER, content="Hello")],
    )

    resp = fake.complete(req)
    assert resp.content == "Hello from fake model"
    assert resp.finish_reason == "stop"
    assert resp.model == "fake-v1"
    assert fake.call_count == 1
    assert fake.last_request == req


def test_fake_provider_structured_tool_calls() -> None:
    tool_call = ToolCall(
        id="call_read_123",
        name="read_file",
        arguments={"path": "src/main.py", "max_bytes": 1024},
    )

    fake = FakeProvider(
        responses=[
            CompletionResponse(
                tool_calls=[tool_call],
                finish_reason="tool_calls",
                model="fake-v1",
            )
        ]
    )

    req = CompletionRequest(
        messages=[ChatMessage(role=MessageRole.USER, content="Inspect code")],
        tools=[
            ToolDeclaration(
                name="read_file",
                description="Read file",
                parameters={"type": "object", "properties": {"path": {"type": "string"}}},
            )
        ],
    )

    resp = fake.complete(req)
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "read_file"
    assert resp.tool_calls[0].arguments == {"path": "src/main.py", "max_bytes": 1024}
    assert resp.finish_reason == "tool_calls"


def test_fake_provider_queued_exceptions() -> None:
    fake = FakeProvider(
        responses=[
            ProviderTimeoutError("Simulated timeout"),
            CompletionResponse(content="Recovered response"),
        ]
    )

    req = CompletionRequest(messages=[ChatMessage(role=MessageRole.USER, content="Test")])

    with pytest.raises(ProviderTimeoutError, match="Simulated timeout"):
        fake.complete(req)

    # Next invocation succeeds
    resp = fake.complete(req)
    assert resp.content == "Recovered response"
    assert fake.call_count == 2


def test_fake_provider_async_acomplete() -> None:
    async def _test() -> None:
        fake = FakeProvider(responses=[CompletionResponse(content="Async response")])
        req = CompletionRequest(messages=[ChatMessage(role=MessageRole.USER, content="Hi")])
        resp = await fake.acomplete(req)
        assert resp.content == "Async response"

    asyncio.run(_test())


# --- Secret Redaction Tests ---


def test_redact_secrets_bearer_and_tokens() -> None:
    secret_text = "Sending request with Bearer sk-ant-api03-abcdef1234567890 to endpoint"
    redacted = redact_secrets(secret_text)
    assert "sk-ant-api03-abcdef1234567890" not in redacted
    assert "Bearer [REDACTED]" in redacted


def test_redact_secrets_openai_keys() -> None:
    raw = "Failed with key sk-proj-12345678901234567890abcdef in response"
    redacted = redact_secrets(raw)
    assert "sk-proj-12345678901234567890abcdef" not in redacted
    assert "[REDACTED]" in redacted


def test_redact_secrets_google_keys() -> None:
    raw = "https://generativelanguage.googleapis.com/v1beta/models?key=AIzaSyA1234567890123456789012345678901"
    redacted = redact_secrets(raw)
    assert "AIzaSyA1234567890123456789012345678901" not in redacted
    assert "[REDACTED]" in redacted


def test_redact_secrets_custom_key() -> None:
    custom_secret = "super-secret-password-xyz"
    raw = f"Error connecting with credential {custom_secret} at localhost"
    redacted = redact_secrets(raw, extra_secrets=[custom_secret])
    assert custom_secret not in redacted
    assert "[REDACTED]" in redacted


def test_redact_headers() -> None:
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer sk-12345678901234567890",
        "X-API-Key": "my-secret-key-12345",
    }
    safe_headers = redact_headers(headers)
    assert safe_headers["Content-Type"] == "application/json"
    assert safe_headers["Authorization"] == "Bearer [REDACTED]"
    assert safe_headers["X-API-Key"] == "[REDACTED]"


# --- OpenAIProvider Adapter Tests ---


def test_openai_provider_missing_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TERMICODE_API_KEY", raising=False)

    with pytest.raises(ProviderAuthError, match="No API key found in environment"):
        OpenAIProvider.from_env()


def test_openai_provider_from_env_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key-1234567890")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://custom.api.com/v1")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")
    monkeypatch.setenv("OPENAI_TIMEOUT", "45")

    provider = OpenAIProvider.from_env()
    assert provider.base_url == "https://custom.api.com/v1"
    assert provider.model == "gpt-4o"
    assert provider.timeout_seconds == 45.0
    # Confirm secret is NEVER exposed in repr
    assert "sk-test-key-1234567890" not in repr(provider)
    assert "[REDACTED]" in repr(provider)


def test_openai_provider_success_text_completion() -> None:
    provider = OpenAIProvider(api_key="sk-test-mock-key-12345")

    mock_response_data = {
        "id": "chatcmpl-123",
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Analysis complete."},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(mock_response_data).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        req = CompletionRequest(
            messages=[ChatMessage(role=MessageRole.USER, content="Hello")],
        )
        resp = provider.complete(req)

        assert resp.content == "Analysis complete."
        assert resp.finish_reason == "stop"
        assert resp.tool_calls == []
        assert resp.usage["total_tokens"] == 15
        assert mock_urlopen.called


def test_openai_provider_success_structured_tool_calls() -> None:
    provider = OpenAIProvider(api_key="sk-test-mock-key-12345")

    mock_response_data = {
        "id": "chatcmpl-tool",
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_abc123",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": json.dumps({"path": "src/core.py"}),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
    }

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(mock_response_data).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        req = CompletionRequest(
            messages=[ChatMessage(role=MessageRole.USER, content="Read core.py")],
        )
        resp = provider.complete(req)

        assert len(resp.tool_calls) == 1
        tc = resp.tool_calls[0]
        assert tc.id == "call_abc123"
        assert tc.name == "read_file"
        # Must be parsed into structured dict
        assert tc.arguments == {"path": "src/core.py"}


# --- Malformed Response Tests ---


def test_openai_provider_malformed_json_response() -> None:
    provider = OpenAIProvider(api_key="sk-test-mock-key-12345")

    mock_resp = MagicMock()
    mock_resp.read.return_value = b"<html>502 Bad Gateway</html>"
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        req = CompletionRequest(messages=[ChatMessage(role=MessageRole.USER, content="Hi")])
        with pytest.raises(MalformedResponseError, match="not valid JSON"):
            provider.complete(req)


def test_openai_provider_missing_choices() -> None:
    provider = OpenAIProvider(api_key="sk-test-mock-key-12345")

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"model": "gpt-4o"}).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        req = CompletionRequest(messages=[ChatMessage(role=MessageRole.USER, content="Hi")])
        with pytest.raises(MalformedResponseError, match="missing required 'choices' array"):
            provider.complete(req)


def test_openai_provider_malformed_tool_call_arguments() -> None:
    provider = OpenAIProvider(api_key="sk-test-mock-key-12345")

    # Arguments contains invalid unparseable JSON
    mock_response_data = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_bad",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": "{invalid-json-content",
                            },
                        }
                    ],
                }
            }
        ]
    }

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(mock_response_data).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        req = CompletionRequest(messages=[ChatMessage(role=MessageRole.USER, content="Hi")])
        with pytest.raises(MalformedResponseError, match="arguments contain malformed JSON"):
            provider.complete(req)


def test_openai_provider_rejects_free_form_command_as_tool_args() -> None:
    provider = OpenAIProvider(api_key="sk-test-mock-key-12345")

    # Model returned a raw string/array instead of structured JSON object
    mock_response_data = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_bad",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": json.dumps(["rm", "-rf", "/"]),  # JSON list, not dict!
                            },
                        }
                    ],
                }
            }
        ]
    }

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(mock_response_data).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        req = CompletionRequest(messages=[ChatMessage(role=MessageRole.USER, content="Hi")])
        with pytest.raises(MalformedResponseError, match="must be a structured JSON object"):
            provider.complete(req)


# --- Error Mapping and Secret Sanitization Tests ---


def test_openai_provider_maps_http_401_auth_error() -> None:
    secret_key = "sk-test-secret-401-key-12345"
    provider = OpenAIProvider(api_key=secret_key)

    http_error = urllib.error.HTTPError(
        url=f"https://api.openai.com/v1/chat/completions?key={secret_key}",
        code=401,
        msg="Unauthorized",
        hdrs=MagicMock(),
        fp=BytesIO(f'{{"error": "Invalid token {secret_key}"}}'.encode()),
    )

    with patch("urllib.request.urlopen", side_effect=http_error):
        req = CompletionRequest(messages=[ChatMessage(role=MessageRole.USER, content="Hi")])
        with pytest.raises(ProviderAuthError) as exc_info:
            provider.complete(req)

        # Confirm key was redacted from the exception string
        err_msg = str(exc_info.value)
        assert secret_key not in err_msg
        assert "[REDACTED]" in err_msg
        assert "401" in err_msg


def test_openai_provider_maps_http_429_rate_limit() -> None:
    provider = OpenAIProvider(api_key="sk-test-rate-limit-12345")

    http_error = urllib.error.HTTPError(
        url="https://api.openai.com/v1/chat/completions",
        code=429,
        msg="Too Many Requests",
        hdrs=MagicMock(),
        fp=BytesIO(b'{"error": "Rate limit reached"}'),
    )

    with patch("urllib.request.urlopen", side_effect=http_error):
        req = CompletionRequest(messages=[ChatMessage(role=MessageRole.USER, content="Hi")])
        with pytest.raises(ProviderRateLimitError, match="429"):
            provider.complete(req)


def test_openai_provider_maps_http_500_connection_error() -> None:
    provider = OpenAIProvider(api_key="sk-test-server-err-12345")

    http_error = urllib.error.HTTPError(
        url="https://api.openai.com/v1/chat/completions",
        code=500,
        msg="Internal Server Error",
        hdrs=MagicMock(),
        fp=BytesIO(b'{"error": "Internal server issue"}'),
    )

    with patch("urllib.request.urlopen", side_effect=http_error):
        req = CompletionRequest(messages=[ChatMessage(role=MessageRole.USER, content="Hi")])
        with pytest.raises(ProviderConnectionError, match="500"):
            provider.complete(req)


def test_openai_provider_maps_timeout() -> None:
    provider = OpenAIProvider(api_key="sk-test-timeout-12345")

    with patch("urllib.request.urlopen", side_effect=TimeoutError("Request timed out")):
        req = CompletionRequest(messages=[ChatMessage(role=MessageRole.USER, content="Hi")])
        with pytest.raises(ProviderTimeoutError, match="timed out"):
            provider.complete(req)


# --- Local LLM & Ollama Provider Tests ---


def test_openai_provider_from_env_ollama_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERMICODE_PROVIDER", "ollama")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TERMICODE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("TERMICODE_API_BASE", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("TERMICODE_MODEL", raising=False)

    provider = OpenAIProvider.from_env()
    assert provider.base_url == "http://localhost:11434/v1"
    assert provider.model == "qwen2.5-coder:7b"
    assert provider.timeout_seconds == 120.0
    assert provider._api_key == "ollama"


def test_openai_provider_from_env_ollama_custom_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERMICODE_PROVIDER", "ollama")
    monkeypatch.setenv("TERMICODE_MODEL", "deepseek-coder-v2:16b")
    monkeypatch.setenv("TERMICODE_API_BASE", "http://127.0.0.1:8000/v1")
    monkeypatch.setenv("TERMICODE_PROVIDER_TIMEOUT", "180.0")

    provider = OpenAIProvider.from_env()
    assert provider.base_url == "http://127.0.0.1:8000/v1"
    assert provider.model == "deepseek-coder-v2:16b"
    assert provider.timeout_seconds == 180.0
    assert provider._api_key == "ollama"


def test_openai_provider_check_local_endpoint_connected() -> None:
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = False

    with patch("urllib.request.urlopen", return_value=mock_resp):
        is_up, msg = OpenAIProvider.check_local_endpoint("http://localhost:11434/v1")
        assert is_up is True
        assert msg == "Connected"


def test_openai_provider_check_local_endpoint_unreachable() -> None:
    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("Connection refused"),
    ):
        is_up, msg = OpenAIProvider.check_local_endpoint("http://localhost:11434/v1")
        assert is_up is False
        assert "Unreachable" in msg
