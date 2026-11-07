"""OpenAI-compatible LLM provider adapter configured exclusively via environment variables."""

from __future__ import annotations

import asyncio
import json
import os
import re
import urllib.error
import urllib.request
from http.client import HTTPException
from typing import Any

from termicode.providers.base import (
    ChatMessage,
    CompletionRequest,
    CompletionResponse,
    LLMProvider,
    MalformedResponseError,
    ProviderAuthError,
    ProviderConnectionError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ToolCall,
    ToolDeclaration,
)
from termicode.providers.redaction import redact_secrets


class OpenAIProvider(LLMProvider):
    """Provider adapter for OpenAI and compatible APIs (vLLM, OpenRouter, Ollama).

    Configured exclusively through environment variables. Never logs API keys or
    sensitive authorization headers. Validates that model responses return typed,
    structured tool calls.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-mini",
        timeout_seconds: float = 30.0,
    ) -> None:
        if not api_key or not api_key.strip():
            raise ProviderAuthError(
                "API key cannot be empty. Configure OPENAI_API_KEY or TERMICODE_API_KEY."
            )
        self._api_key = api_key.strip()
        self.base_url = base_url.strip().rstrip("/")
        self.model = model.strip()
        self.timeout_seconds = timeout_seconds

    @classmethod
    def check_local_endpoint(
        cls,
        base_url: str = "http://localhost:11434/v1",
        timeout_seconds: float = 1.5,
    ) -> tuple[bool, str]:
        """Probe local endpoint (e.g. Ollama/vLLM) to determine connectivity status."""
        probe_url = f"{base_url.rstrip('/')}/models"
        req = urllib.request.Request(
            probe_url,
            headers={"Authorization": "Bearer ollama", "User-Agent": "TermiCode/0.1.0"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                if 200 <= resp.status < 300:
                    return True, "Connected"
                return False, f"HTTP {resp.status}"
        except urllib.error.HTTPError as err:
            # 401 or 404 still indicates the server is listening
            if err.code in (401, 403, 404):
                return True, f"Server responding (HTTP {err.code})"
            return False, f"HTTP Error {err.code}"
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            return False, f"Unreachable ({err})"

    @classmethod
    def from_env(cls, provider_type: str | None = None) -> OpenAIProvider:
        """Construct an OpenAIProvider instance from env vars, supporting Ollama & OpenAI."""
        provider = (provider_type or os.environ.get("TERMICODE_PROVIDER", "openai")).strip().lower()

        # Handle local Ollama provider with zero-key auto-configuration
        is_local_provider = provider in ("ollama", "local")
        explicit_base = (
            os.environ.get("OPENAI_BASE_URL")
            or os.environ.get("OPENAI_API_BASE")
            or os.environ.get("TERMICODE_API_BASE")
        )
        is_local_endpoint = bool(
            explicit_base and ("localhost" in explicit_base or "127.0.0.1" in explicit_base)
        )

        if is_local_provider or is_local_endpoint:
            base_url = explicit_base or "http://localhost:11434/v1"
            model = (
                os.environ.get("OPENAI_MODEL")
                or os.environ.get("TERMICODE_MODEL")
                or "qwen2.5-coder:7b"
            )
            api_key = (
                os.environ.get("OPENAI_API_KEY")
                or os.environ.get("TERMICODE_API_KEY")
                or "ollama"  # Default token for local endpoints
            )
            raw_timeout = (
                os.environ.get("OPENAI_TIMEOUT")
                or os.environ.get("TERMICODE_PROVIDER_TIMEOUT")
                or "120.0"
            )
        else:
            api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("TERMICODE_API_KEY") or ""
            if not api_key.strip():
                raise ProviderAuthError(
                    "Provider authentication failed: No API key found in environment. "
                    "Set OPENAI_API_KEY or TERMICODE_API_KEY."
                )

            base_url = explicit_base or "https://api.openai.com/v1"
            model = (
                os.environ.get("OPENAI_MODEL") or os.environ.get("TERMICODE_MODEL") or "gpt-4o-mini"
            )
            raw_timeout = (
                os.environ.get("OPENAI_TIMEOUT")
                or os.environ.get("TERMICODE_PROVIDER_TIMEOUT")
                or "30.0"
            )

        try:
            timeout = float(raw_timeout)
        except ValueError:
            timeout = 120.0 if (is_local_provider or is_local_endpoint) else 30.0

        return cls(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout_seconds=timeout,
        )

    def __repr__(self) -> str:
        """Represent provider without exposing the secret API key."""
        return (
            f"OpenAIProvider(model='{self.model}', base_url='{self.base_url}', "
            f"api_key='[REDACTED]')"
        )

    def _sanitize(self, text: str) -> str:
        """Scrub secret credentials from any string before raising or logging."""
        return redact_secrets(text, extra_secrets=[self._api_key])

    def _format_messages(self, messages: list[ChatMessage]) -> list[dict[str, Any]]:
        """Convert typed ChatMessage sequence to provider wire format."""
        formatted: list[dict[str, Any]] = []
        for msg in messages:
            entry: dict[str, Any] = {"role": msg.role.value}
            if msg.content is not None:
                entry["content"] = msg.content

            if msg.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in msg.tool_calls
                ]

            if msg.tool_call_id:
                entry["tool_call_id"] = msg.tool_call_id

            if msg.name:
                entry["name"] = msg.name

            formatted.append(entry)
        return formatted

    def _format_tools(self, tools: list[ToolDeclaration]) -> list[dict[str, Any]]:
        """Convert tool declarations into OpenAI function-calling specifications."""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in tools
        ]

    def _build_request_payload(self, request: CompletionRequest) -> bytes:
        """Construct JSON payload bytes for completions endpoint."""
        payload: dict[str, Any] = {
            "model": request.model or self.model,
            "messages": self._format_messages(request.messages),
            "temperature": request.temperature,
        }
        if request.tools:
            payload["tools"] = self._format_tools(request.tools)
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens

        return json.dumps(payload).encode("utf-8")

    def _parse_response(self, raw_body: str) -> CompletionResponse:
        """Parse and strictly validate raw JSON response from provider."""
        try:
            data = json.loads(raw_body)
        except json.JSONDecodeError as err:
            raise MalformedResponseError(
                f"Provider response is not valid JSON: {self._sanitize(raw_body)}"
            ) from err

        if not isinstance(data, dict):
            raise MalformedResponseError(
                f"Expected JSON object response, got {type(data).__name__}"
            )

        if "choices" not in data or not isinstance(data["choices"], list) or not data["choices"]:
            raise MalformedResponseError(
                f"Provider response missing required 'choices' array: {self._sanitize(raw_body)}"
            )

        choice = data["choices"][0]
        if not isinstance(choice, dict):
            raise MalformedResponseError(
                f"Expected choice object in 'choices', got {type(choice).__name__}"
            )

        message = choice.get("message")
        if not isinstance(message, dict):
            raise MalformedResponseError(
                f"Choice missing 'message' dictionary: {self._sanitize(raw_body)}"
            )

        content: str | None = message.get("content")
        finish_reason: str = str(choice.get("finish_reason") or "stop")
        model: str = str(data.get("model") or self.model)
        raw_usage = data.get("usage")
        usage: dict[str, int] = (
            {str(k): int(v) for k, v in raw_usage.items() if isinstance(v, (int, float))}
            if isinstance(raw_usage, dict)
            else {}
        )

        # Strictly validate and parse structured tool calls
        validated_tool_calls: list[ToolCall] = []
        raw_tool_calls = message.get("tool_calls")

        if raw_tool_calls is not None:
            if not isinstance(raw_tool_calls, list):
                raise MalformedResponseError(
                    f"Expected list for 'tool_calls', got {type(raw_tool_calls).__name__}"
                )

            for idx, tc in enumerate(raw_tool_calls):
                if not isinstance(tc, dict):
                    raise MalformedResponseError(
                        f"Tool call at index {idx} is not an object: {self._sanitize(str(tc))}"
                    )

                tc_id = str(tc.get("id") or f"call_{idx}")
                func = tc.get("function")
                if not isinstance(func, dict):
                    raise MalformedResponseError(f"Tool call '{tc_id}' missing 'function' object.")

                tool_name = func.get("name")
                if not tool_name or not isinstance(tool_name, str):
                    raise MalformedResponseError(
                        f"Tool call '{tc_id}' function missing valid string name."
                    )

                raw_arguments = func.get("arguments", "{}")
                if not isinstance(raw_arguments, (str, dict)):
                    raise MalformedResponseError(
                        f"Tool call '{tool_name}' arguments must be a JSON string or dict, "
                        f"got {type(raw_arguments).__name__}."
                    )

                # Parse arguments into structured dictionary
                if isinstance(raw_arguments, str):
                    cleaned_args = raw_arguments.strip()
                    if cleaned_args.startswith("```"):
                        cleaned_args = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned_args)
                        cleaned_args = re.sub(r"\s*```$", "", cleaned_args).strip()
                    try:
                        parsed_args = json.loads(cleaned_args)
                    except json.JSONDecodeError as err:
                        raise MalformedResponseError(
                            f"Tool call '{tool_name}' arguments contain malformed JSON: "
                            f"{self._sanitize(raw_arguments)}"
                        ) from err
                else:
                    parsed_args = raw_arguments

                if not isinstance(parsed_args, dict):
                    raise MalformedResponseError(
                        f"Tool call '{tool_name}' arguments must be a structured JSON object, "
                        f"not a free-form value ({type(parsed_args).__name__})."
                    )

                validated_tool_calls.append(
                    ToolCall(
                        id=tc_id,
                        name=tool_name,
                        arguments=parsed_args,
                    )
                )

        # Resilient fallback: parse tool calls emitted in content by open-weights local LLMs
        if not validated_tool_calls and content and "{" in content:
            fallback_tc = self._extract_fallback_tool_call(content)
            if fallback_tc:
                validated_tool_calls.append(fallback_tc)

        return CompletionResponse(
            content=content,
            tool_calls=validated_tool_calls,
            finish_reason=finish_reason,
            model=model,
            usage=usage,
            metadata={"raw": data},
        )

    def _extract_fallback_tool_call(self, text: str) -> ToolCall | None:
        """Extract tool call from markdown or XML blocks if local LLM outputs it in text."""
        # 1. Check <tool_call> ... </tool_call>
        match = re.search(r"<tool_call>\s*({.*?})\s*</tool_call>", text, re.DOTALL)
        if not match:
            # 2. Check ```json ... ``` containing "name"
            match = re.search(r'```(?:json)?\s*({.*?"name"\s*:.*?})\s*```', text, re.DOTALL)
        if not match:
            return None

        try:
            data = json.loads(match.group(1))
            if isinstance(data, dict) and "name" in data and isinstance(data["name"], str):
                args = data.get("arguments", {})
                if isinstance(args, str):
                    args = json.loads(args)
                if isinstance(args, dict):
                    return ToolCall(
                        id="call_local_0",
                        name=data["name"],
                        arguments=args,
                    )
        except (json.JSONDecodeError, TypeError, KeyError):
            return None
        return None

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Synchronously execute completion with error mapping and secret redaction."""
        endpoint = f"{self.base_url}/chat/completions"
        payload = self._build_request_payload(request)

        http_request = urllib.request.Request(
            url=endpoint,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                body_bytes = response.read()
                body_text = body_bytes.decode("utf-8", errors="replace")

        except urllib.error.HTTPError as err:
            err_body = ""
            try:
                err_body = err.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            clean_msg = self._sanitize(f"HTTP {err.code}: {err.reason} - {err_body}".strip())

            if err.code in (401, 403):
                raise ProviderAuthError(clean_msg) from err
            elif err.code == 429:
                raise ProviderRateLimitError(clean_msg) from err
            elif err.code >= 500:
                raise ProviderConnectionError(clean_msg) from err
            else:
                raise ProviderError(clean_msg) from err

        except TimeoutError as err:
            raise ProviderTimeoutError(
                self._sanitize(
                    f"Request to {endpoint} timed out after {self.timeout_seconds}s: {err}"
                )
            ) from err

        except urllib.error.URLError as err:
            reason_str = str(err.reason).lower()
            if "timed out" in reason_str:
                raise ProviderTimeoutError(
                    self._sanitize(f"Connection to {endpoint} timed out: {err.reason}")
                ) from err
            raise ProviderConnectionError(
                self._sanitize(f"Failed to connect to provider at {endpoint}: {err.reason}")
            ) from err

        except HTTPException as err:
            raise ProviderConnectionError(
                self._sanitize(f"HTTP transport exception: {err}")
            ) from err

        except Exception as err:
            raise ProviderError(self._sanitize(f"Unexpected provider error: {err}")) from err

        return self._parse_response(body_text)

    async def acomplete(self, request: CompletionRequest) -> CompletionResponse:
        """Asynchronously execute a completion request in a worker thread."""
        return await asyncio.to_thread(self.complete, request)
