"""Typed configuration settings using Pydantic Settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration settings for TermiCode AI runtime."""

    model_config = SettingsConfigDict(
        env_prefix="TERMICODE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    workspace: Path = Field(
        default_factory=Path.cwd,
        description="Approved workspace root directory.",
    )
    environment: str = Field(
        default="development",
        description="Runtime environment (e.g., development, test, production).",
    )
    log_level: str = Field(
        default="INFO",
        description="Logging verbosity level.",
    )
    max_steps: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum tool iterations permitted per task run.",
    )
    max_repairs: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Maximum consecutive repair attempts allowed when actions fail.",
    )
    command_timeout_seconds: int = Field(
        default=30,
        ge=1,
        le=300,
        description="Maximum execution timeout for allowlisted commands.",
    )
    max_command_output_bytes: int = Field(
        default=65536,
        ge=1024,
        le=1048576,
        description="Maximum capture bytes for command stdout/stderr.",
    )
    provider: str = Field(
        default="openai",
        description="Active LLM provider ('openai', 'ollama', or 'fake').",
    )
    model: str = Field(
        default="gpt-4o-mini",
        description="Active model identifier.",
    )
    api_base: str | None = Field(
        default=None,
        description="Optional custom API base URL for local servers (Ollama/vLLM).",
    )
    provider_timeout_seconds: float = Field(
        default=60.0,
        ge=5.0,
        le=600.0,
        description="HTTP request timeout for LLM provider completions.",
    )

    @classmethod
    def load(
        cls,
        workspace: Path | str | None = None,
        **overrides: Any,
    ) -> Settings:
        """Load settings from environment/.env with optional runtime overrides."""
        kwargs: dict[str, Any] = {**overrides}
        if workspace is not None:
            kwargs["workspace"] = Path(workspace)
        return cls(**kwargs)
