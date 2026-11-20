"""Data models and session structures for TermiCode AI Chatbot."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class ChatMessageRecord(BaseModel):
    """Represents a single message turn in the conversation."""

    role: str = Field(description="Role of the speaker ('user', 'assistant', 'system').")
    content: str = Field(description="Textual content of the message.")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(UTC).strftime("%H:%M:%S"),
        description="Formatted time when message was created.",
    )
    step_logs: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Intermediate planning and tool step logs generated during turn.",
    )


class AttachedDocument(BaseModel):
    """Represents an attached file (PDF, notes, code) within the chat session."""

    doc_id: str = Field(description="Unique identifier for the document in this session.")
    path: str = Field(description="Canonical path of the attached document.")
    filename: str = Field(description="Basename of the file.")
    size_bytes: int = Field(description="File size in bytes.")
    file_type: str = Field(description="File extension or classification.")
    uploaded_at: str = Field(
        default_factory=lambda: datetime.now(UTC).strftime("%H:%M:%S"),
        description="Timestamp when attached.",
    )


class ChatSessionState(BaseModel):
    """Complete snapshot of active chat session state."""

    session_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    messages: list[ChatMessageRecord] = Field(default_factory=list)
    documents: list[AttachedDocument] = Field(default_factory=list)
    provider: str = Field(default="ollama")
    model: str = Field(default="qwen2.5-coder:7b")
    toolbridge_enabled: bool = Field(default=False)
    toolbridge_url: str = Field(default="http://127.0.0.1:8000")
    verbose_steps: bool = Field(default=True)
