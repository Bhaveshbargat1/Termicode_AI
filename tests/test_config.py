"""Tests for typed settings and configuration loading."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from termicode.config import Settings


def test_settings_defaults() -> None:
    settings = Settings()
    assert settings.environment == "development"
    assert settings.log_level == "INFO"
    assert settings.max_steps == 10
    assert settings.max_repairs == 3
    assert settings.command_timeout_seconds == 30
    assert settings.max_command_output_bytes == 65536
    assert isinstance(settings.workspace, Path)


def test_settings_load_with_overrides(tmp_path: Path) -> None:
    settings = Settings.load(
        workspace=tmp_path,
        environment="test",
        max_steps=5,
        max_repairs=1,
    )
    assert settings.workspace == tmp_path
    assert settings.environment == "test"
    assert settings.max_steps == 5
    assert settings.max_repairs == 1


def test_settings_validation_bounds() -> None:
    # max_steps must be >= 1
    with pytest.raises(ValidationError):
        Settings(max_steps=0)

    # max_repairs must be >= 0
    with pytest.raises(ValidationError):
        Settings(max_repairs=-1)

    # command_timeout_seconds must be >= 1
    with pytest.raises(ValidationError):
        Settings(command_timeout_seconds=0)

    # max_command_output_bytes must be >= 1024
    with pytest.raises(ValidationError):
        Settings(max_command_output_bytes=100)


def test_settings_provider_defaults_and_bounds() -> None:
    settings = Settings()
    assert settings.provider == "openai"
    assert settings.model == "gpt-4o-mini"
    assert settings.api_base is None
    assert settings.provider_timeout_seconds == 60.0

    # Test custom values
    custom = Settings(
        provider="ollama",
        model="qwen2.5-coder:7b",
        api_base="http://localhost:11434/v1",
        provider_timeout_seconds=120.0,
    )
    assert custom.provider == "ollama"
    assert custom.model == "qwen2.5-coder:7b"
    assert custom.api_base == "http://localhost:11434/v1"
    assert custom.provider_timeout_seconds == 120.0

    # Timeout must be >= 5.0
    with pytest.raises(ValidationError):
        Settings(provider_timeout_seconds=2.0)
