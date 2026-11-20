"""Unit tests for TermiCode AI Chatbot subsystem, REPL loop, and CLI integration."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

from termicode.chat import (
    ChatEngine,
    ChatREPL,
    ChatSession,
    DocumentNotFoundError,
)
from termicode.cli import app
from termicode.config import Settings
from termicode.workspace import WorkspaceBoundary


@pytest.fixture
def test_workspace(tmp_path: Path) -> WorkspaceBoundary:
    """Fixture providing an isolated WorkspaceBoundary."""
    ws = tmp_path / "chat_workspace"
    ws.mkdir(parents=True, exist_ok=True)
    boundary = WorkspaceBoundary(ws)
    boundary.ensure_termicode_dir()
    return boundary


@pytest.fixture
def chat_session(test_workspace: WorkspaceBoundary) -> ChatSession:
    """Fixture providing a clean ChatSession."""
    settings = Settings.load(workspace=test_workspace.root)
    settings.provider = "fake"
    return ChatSession(workspace=test_workspace, settings=settings, verbose_steps=True)


def test_chat_session_message_management(chat_session: ChatSession) -> None:
    """Verify adding messages, timestamps, and clearing history."""
    assert len(chat_session.get_history()) == 0

    user_msg = chat_session.add_user_message("Hello TermiCode")
    assert user_msg.role == "user"
    assert user_msg.content == "Hello TermiCode"
    assert user_msg.timestamp is not None

    asst_msg = chat_session.add_assistant_message(
        "Hello Developer", step_logs=[{"phase": "Planner", "action": "Test"}]
    )
    assert asst_msg.role == "assistant"
    assert asst_msg.content == "Hello Developer"
    assert len(asst_msg.step_logs) == 1

    history = chat_session.get_history()
    assert len(history) == 2

    # Clear chat
    chat_session.new_chat()
    assert len(chat_session.get_history()) == 0


def test_document_attachment_and_management(
    chat_session: ChatSession, test_workspace: WorkspaceBoundary
) -> None:
    """Verify attaching, listing, and removing documents."""
    sample_file = test_workspace.root / "architecture_notes.md"
    sample_file.write_text("# Project Notes\nArchitecture details.", encoding="utf-8")

    # Add document
    doc = chat_session.add_document(sample_file)
    assert doc.doc_id == "doc_1"
    assert doc.filename == "architecture_notes.md"
    assert doc.file_type == ".md"
    assert doc.size_bytes > 0

    # Duplicate check
    doc_dup = chat_session.add_document(sample_file)
    assert doc_dup.doc_id == doc.doc_id
    assert len(chat_session.list_documents()) == 1

    # Attach second file
    spec_file = test_workspace.root / "specs.pdf"
    spec_file.write_bytes(b"%PDF-1.4 dummy content")
    doc2 = chat_session.add_document("specs.pdf")
    assert doc2.doc_id == "doc_2"
    assert len(chat_session.list_documents()) == 2

    # Non-existent file error
    with pytest.raises(DocumentNotFoundError):
        chat_session.add_document("non_existent_file.txt")

    # Remove document by doc_id
    removed = chat_session.remove_document("doc_1")
    assert removed is True
    assert len(chat_session.list_documents()) == 1

    # Remove document by filename
    removed_fn = chat_session.remove_document("specs.pdf")
    assert removed_fn is True
    assert len(chat_session.list_documents()) == 0

    # Non-existent removal returns False
    assert chat_session.remove_document("doc_999") is False


def test_chat_session_settings_update(chat_session: ChatSession) -> None:
    """Verify runtime settings modification via session interface."""
    ok, msg = chat_session.update_setting("model", "gpt-4o")
    assert ok is True
    assert chat_session.state.model == "gpt-4o"

    ok, msg = chat_session.update_setting("provider", "fake")
    assert ok is True
    assert chat_session.state.provider == "fake"

    ok, msg = chat_session.update_setting("provider", "invalid_prov")
    assert ok is False

    ok, msg = chat_session.update_setting("toolbridge", "true")
    assert ok is True
    assert chat_session.state.toolbridge_enabled is True

    ok, msg = chat_session.update_setting("steps", "off")
    assert ok is True
    assert chat_session.state.verbose_steps is False

    ok, msg = chat_session.update_setting("unknown_key", "val")
    assert ok is False


def test_chat_engine_response_and_streaming(
    chat_session: ChatSession, test_workspace: WorkspaceBoundary
) -> None:
    """Verify chat engine response formulation, step logs, and streaming."""
    engine = ChatEngine(session=chat_session, boundary=test_workspace)

    # General greeting
    reply, steps = engine.generate_response("hello")
    assert "TermiCode AI" in reply
    assert len(steps) >= 2
    assert any(s["phase"] == "Planner" for s in steps)

    # Code generation query
    code_reply, _ = engine.generate_response("create a python function")
    assert "```python" in code_reply

    # Streaming chunks
    chunks = list(engine.stream_chunks("Hello world from TermiCode", chunk_delay=0.0))
    assert "".join(chunks) == "Hello world from TermiCode"


def test_chat_repl_slash_commands(
    chat_session: ChatSession, test_workspace: WorkspaceBoundary
) -> None:
    """Verify ChatREPL command parsing, doc listings, and exit conditions."""
    output_buffer = io.StringIO()
    test_console = Console(file=output_buffer, force_terminal=False, width=120)

    engine = ChatEngine(session=chat_session, boundary=test_workspace)
    repl = ChatREPL(
        engine=engine,
        console=test_console,
        enable_streaming_animation=False,
    )

    # /help
    assert repl.handle_command("/help") is True
    assert "Commands & Controls" in output_buffer.getvalue()

    # /settings
    assert repl.handle_command("/settings") is True
    assert "Session Configuration" in output_buffer.getvalue()

    # /settings key=value
    assert repl.handle_command("/settings model=claude-3-5") is True

    # /steps toggle
    assert repl.handle_command("/steps off") is True
    assert chat_session.state.verbose_steps is False

    # /upload
    test_doc = test_workspace.root / "sample.txt"
    test_doc.write_text("sample content", encoding="utf-8")
    assert repl.handle_command(f"/upload {test_doc}") is True
    assert "Attached:" in output_buffer.getvalue()

    # /docs
    assert repl.handle_command("/docs") is True
    assert "sample.txt" in output_buffer.getvalue()

    # /drop
    assert repl.handle_command("/drop doc_1") is True

    # /new
    assert repl.handle_command("/new") is True
    assert len(chat_session.get_history()) == 0

    # Conversational query
    assert repl.handle_command("explain project architecture") is True

    # /exit
    assert repl.handle_command("/exit") is False
    assert repl.handle_command("exit") is False


def test_cli_init_command(tmp_path: Path) -> None:
    """Verify 'termicode init' initializes .termicode/ and .env."""
    runner = CliRunner()
    result = runner.invoke(app, ["-w", str(tmp_path), "init"])
    assert result.exit_code == 0
    assert "TermiCode initialized in" in result.output
    assert (tmp_path / ".termicode").is_dir()
    assert (tmp_path / ".env").is_file()


def test_cli_chat_command_exit(tmp_path: Path) -> None:
    """Verify 'termicode chat --offline' launches REPL and exits cleanly."""
    runner = CliRunner()
    # Provide 'exit\n' to terminate REPL immediately
    result = runner.invoke(app, ["-w", str(tmp_path), "chat", "--offline"], input="exit\n")
    assert result.exit_code == 0
    assert "Interactive Chat Session" in result.output
    assert "Goodbye" in result.output


def test_cli_serve_command_exit(tmp_path: Path) -> None:
    """Verify 'termicode serve --offline' alias functions identically."""
    runner = CliRunner()
    result = runner.invoke(app, ["-w", str(tmp_path), "serve", "--offline"], input="exit\n")
    assert result.exit_code == 0
    assert "Interactive Chat Session" in result.output
