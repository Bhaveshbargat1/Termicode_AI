"""Secret redaction utilities to prevent leaking API keys and credentials in logs."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Final

# Regex patterns matching known API key and authorization formats
_REDACTION_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    # Bearer tokens in headers or logs
    re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9_\-\.]{8,}"),
    # OpenAI and compatible API keys (sk-...)
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"),
    # Google Gemini API keys (AIza...)
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    # Generic key patterns like key-..., secret-..., token-...
    re.compile(r"\b(?:key|token|secret)-[A-Za-z0-9_\-]{16,}\b"),
    # URL query parameters (e.g. ?key=secret or &api_key=secret)
    re.compile(r"(?i)([?&](?:api_key|key|token|auth)=)[^&\s]+"),
)

SENSITIVE_HEADER_KEYS: Final[frozenset[str]] = frozenset(
    {
        "authorization",
        "x-api-key",
        "api-key",
        "x-auth-token",
        "proxy-authorization",
    }
)


def redact_secrets(
    text: str,
    extra_secrets: Iterable[str] | None = None,
    replacement: str = "[REDACTED]",
) -> str:
    """Sanitize text by replacing API keys, tokens, and authorization values.

    Args:
        text: Input string that may contain sensitive credentials.
        extra_secrets: Optional specific secret strings (e.g. current API key) to mask.
        replacement: Replacement text for masked credentials.

    Returns:
        Redacted text safe for logging, printing, and error display.
    """
    if not text:
        return ""

    sanitized = text

    # Redact explicit custom secrets first
    if extra_secrets:
        for secret in extra_secrets:
            if secret and len(secret.strip()) >= 4:
                sanitized = sanitized.replace(secret.strip(), replacement)

    # Redact standard credential patterns
    for pattern in _REDACTION_PATTERNS:
        # If pattern has a capturing group (like 'Bearer ' or '?key='), preserve prefix
        if pattern.groups > 0:
            sanitized = pattern.sub(rf"\g<1>{replacement}", sanitized)
        else:
            sanitized = pattern.sub(replacement, sanitized)

    return sanitized


def redact_headers(
    headers: Mapping[str, str],
    replacement: str = "[REDACTED]",
) -> dict[str, str]:
    """Return a sanitized dictionary of HTTP headers with sensitive values masked."""
    sanitized: dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in SENSITIVE_HEADER_KEYS:
            if key.lower() == "authorization" and value.lower().startswith("bearer "):
                sanitized[key] = f"Bearer {replacement}"
            else:
                sanitized[key] = replacement
        else:
            sanitized[key] = redact_secrets(value, replacement=replacement)
    return sanitized
