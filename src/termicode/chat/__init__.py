"""Chatbot interface subsystem for TermiCode AI."""

from termicode.chat.database import (
    SessionDatabase,
    SessionDatabaseError,
    SessionNotFoundError,
)
from termicode.chat.engine import ChatEngine
from termicode.chat.models import AttachedDocument, ChatMessageRecord, ChatSessionState
from termicode.chat.repl import ChatREPL
from termicode.chat.session import ChatSession, ChatSessionError, DocumentNotFoundError
from termicode.chat.summarizer import MemorySummarizerStub

__all__ = [
    "AttachedDocument",
    "ChatEngine",
    "ChatMessageRecord",
    "ChatREPL",
    "ChatSession",
    "ChatSessionError",
    "ChatSessionState",
    "DocumentNotFoundError",
    "MemorySummarizerStub",
    "SessionDatabase",
    "SessionDatabaseError",
    "SessionNotFoundError",
]
