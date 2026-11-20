"""Unit and integration tests for persistent conversation sessions and memory in TermiCode AI."""

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
    MemorySummarizerStub,
    SessionDatabase,
    SessionNotFoundError,
)
from termicode.cli import app
from termicode.config import Settings
from termicode.workspace import WorkspaceBoundary


@pytest.fixture
def test_workspace(tmp_path: Path) -> WorkspaceBoundary:
    """Provide an isolated WorkspaceBoundary with .termicode directory initialized."""
    ws = tmp_path / "session_workspace"
    ws.mkdir(parents=True, exist_ok=True)
    boundary = WorkspaceBoundary(ws)
    boundary.ensure_termicode_dir()
    return boundary


@pytest.fixture
def session_db(test_workspace: WorkspaceBoundary) -> SessionDatabase:
    """Provide an isolated SQLite SessionDatabase instance."""
    db_path = test_workspace.termicode_dir / "chat_sessions.db"
    return SessionDatabase(db_path)


# ---------------------------------------------------------------------------
# SessionDatabase Unit Tests
# ---------------------------------------------------------------------------


def test_session_db_initialization_and_creation(session_db: SessionDatabase) -> None:
    """Verify session creation, default IDs, custom IDs, and retrieval."""
    # Create with auto-generated ID
    sid1 = session_db.create_session(title="First Exploration")
    assert sid1.startswith("sess_")

    meta1 = session_db.get_session(sid1)
    assert meta1 is not None
    assert meta1["id"] == sid1
    assert meta1["title"] == "First Exploration"
    assert meta1["is_active"] is True
    assert meta1["message_count"] == 0

    # Create second session with explicit ID and check active flag switch
    sid2 = session_db.create_session(session_id="sess_custom_99", title="Second Exploration")
    assert sid2 == "sess_custom_99"

    meta2 = session_db.get_session(sid2)
    assert meta2 is not None
    assert meta2["is_active"] is True

    # First session is no longer active
    meta1_updated = session_db.get_session(sid1)
    assert meta1_updated is not None
    assert meta1_updated["is_active"] is False

    # Get active session
    active = session_db.get_active_session()
    assert active is not None
    assert active["id"] == sid2


def test_session_db_list_and_switch(session_db: SessionDatabase) -> None:
    """Verify listing sessions in chronological order and switching active session."""
    sid_a = session_db.create_session(session_id="sess_a", title="Session A")
    sid_b = session_db.create_session(session_id="sess_b", title="Session B")
    assert sid_b == "sess_b"

    sessions = session_db.list_sessions()
    assert len(sessions) >= 2
    session_ids = [s["id"] for s in sessions]
    assert "sess_a" in session_ids
    assert "sess_b" in session_ids

    # Switch back to A
    switched = session_db.set_active_session(sid_a)
    assert switched is True

    active = session_db.get_active_session()
    assert active is not None
    assert active["id"] == sid_a

    # Switching to non-existent returns False
    assert session_db.set_active_session("sess_non_existent") is False


def test_session_db_delete_and_cascade(session_db: SessionDatabase) -> None:
    """Verify deleting a session removes messages and reassigns active session."""
    sid1 = session_db.create_session(session_id="sess_del_1", title="To Delete")
    sid2 = session_db.create_session(session_id="sess_del_2", title="Survivor")

    session_db.add_message(sid1, role="user", content="Will be deleted")
    session_db.add_message(sid2, role="user", content="Will survive")

    assert session_db.get_message_count(sid1) == 1
    assert session_db.get_message_count(sid2) == 1

    # Delete sid2 (which is currently active)
    deleted = session_db.delete_session(sid2)
    assert deleted is True
    assert session_db.get_session(sid2) is None
    assert len(session_db.get_messages(sid2)) == 0

    # sid1 should now be active
    active = session_db.get_active_session()
    assert active is not None
    assert active["id"] == sid1

    # Deleting non-existent returns False
    assert session_db.delete_session("sess_ghost") is False


def test_session_db_secret_redaction_in_messages(session_db: SessionDatabase) -> None:
    """Verify that credentials and API keys are strictly redacted before SQLite storage."""
    sid = session_db.create_session(session_id="sess_sec", title="Security Test")

    # Sensitive query containing OpenAI key and Bearer token
    secret_text = (
        "Configure OpenAI key sk-proj-1234567890abcdef1234567890abcdef "
        "and header Bearer eyJhbGciOiJIUzI1NiJ9.test"
    )
    session_db.add_message(
        sid,
        role="user",
        content=secret_text,
        step_logs=[{"secret": "AIzaSyD-1234567890abcdef123456789012345"}],
    )

    messages = session_db.get_messages(sid)
    assert len(messages) == 1
    stored_content = messages[0]["content"]

    # Plaintext secrets MUST NOT exist in DB
    assert "sk-proj-1234567890abcdef1234567890abcdef" not in stored_content
    assert "eyJhbGciOiJIUzI1NiJ9" not in stored_content
    assert "[REDACTED]" in stored_content

    # Step logs also redacted
    steps = messages[0]["step_logs"]
    assert "AIzaSyD" not in str(steps)
    assert "[REDACTED]" in str(steps)


def test_session_db_auto_titling(session_db: SessionDatabase) -> None:
    """Verify first user query updates generic 'New Session' title."""
    sid = session_db.create_session(title="New Session")
    session_db.add_message(sid, role="user", content="Implement OAuth2 authentication gateway")

    sess = session_db.get_session(sid)
    assert sess is not None
    assert sess["title"] == "Implement OAuth2 authentication gateway"


def test_session_db_nonexistent_session_raises(session_db: SessionDatabase) -> None:
    """Verify adding a message to non-existent session raises SessionNotFoundError."""
    with pytest.raises(SessionNotFoundError):
        session_db.add_message("sess_unknown", role="user", content="Hello")


def test_session_db_pruning_and_summary(session_db: SessionDatabase) -> None:
    """Verify prune_older_messages deletes old records and returns pruned subset."""
    sid = session_db.create_session(session_id="sess_prune")

    for i in range(10):
        session_db.add_message(sid, role="user" if i % 2 == 0 else "assistant", content=f"Turn {i}")

    assert session_db.get_message_count(sid) == 10

    # Prune keeping last 4 messages
    pruned = session_db.prune_older_messages(sid, keep_last=4)
    assert len(pruned) == 6
    assert pruned[0]["content"] == "Turn 0"
    assert pruned[5]["content"] == "Turn 5"

    # Remaining in DB should be exactly 4
    remaining = session_db.get_messages(sid)
    assert len(remaining) == 4
    assert remaining[0]["content"] == "Turn 6"
    assert remaining[3]["content"] == "Turn 9"

    # Update summary
    session_db.update_summary(sid, "Summary of turns 0 to 5.")
    sess = session_db.get_session(sid)
    assert sess is not None
    assert sess["summary"] == "Summary of turns 0 to 5."


# ---------------------------------------------------------------------------
# MemorySummarizerStub Unit Tests
# ---------------------------------------------------------------------------


def test_memory_summarizer_stub_dialogue_condensation() -> None:
    """Verify MemorySummarizerStub structures dialogue turns and handles existing summaries."""
    summarizer = MemorySummarizerStub(provider=None)

    turns = [
        {"role": "user", "content": "How do I build a Docker image for Python?"},
        {"role": "assistant", "content": "Use a multi-stage Dockerfile with python:3.11-slim."},
    ]

    summary = summarizer.summarize(turns)
    assert "Condensed memory of 2 earlier turn(s)" in summary
    assert "Docker image for Python" in summary

    # Append new summary with existing summary
    new_turns = [{"role": "user", "content": "Now add health checks to docker-compose"}]
    combined = summarizer.summarize(new_turns, existing_summary=summary)
    assert summary in combined
    assert "health checks" in combined


# ---------------------------------------------------------------------------
# ChatSession & Persistence Integration Tests
# ---------------------------------------------------------------------------


def test_chat_session_persistence_across_reloads(test_workspace: WorkspaceBoundary) -> None:
    """Verify conversation messages persist to SQLite and are restored when reloaded."""
    settings = Settings.load(workspace=test_workspace.root)

    # 1. Create first ChatSession instance and send turns
    session1 = ChatSession(workspace=test_workspace, settings=settings)
    sid1 = session1.state.session_id
    session1.add_user_message("What is the architecture of ToolBridge?")
    session1.add_assistant_message("ToolBridge is a dual-mode MCP gateway.")

    assert len(session1.get_history()) == 2

    # 2. Simulate restarting application by creating new ChatSession
    # pointing to same workspace & session
    session2 = ChatSession(workspace=test_workspace, settings=settings, session_id=sid1)
    assert session2.state.session_id == sid1
    history2 = session2.get_history()
    assert len(history2) == 2
    assert history2[0].role == "user"
    assert history2[0].content == "What is the architecture of ToolBridge?"
    assert history2[1].role == "assistant"
    assert history2[1].content == "ToolBridge is a dual-mode MCP gateway."


def test_chat_session_switching_and_isolation(test_workspace: WorkspaceBoundary) -> None:
    """Verify switching between sessions maintains strict message isolation."""
    settings = Settings.load(workspace=test_workspace.root)
    chat = ChatSession(workspace=test_workspace, settings=settings)

    # Session A
    sid_a = chat.state.session_id
    chat.add_user_message("Query A1")
    chat.add_assistant_message("Reply A1")

    # Start Session B
    sid_b = chat.new_chat(title="Session B")
    assert sid_b != sid_a
    assert len(chat.get_history()) == 0

    chat.add_user_message("Query B1")
    assert len(chat.get_history()) == 1
    assert chat.get_history()[0].content == "Query B1"

    # Switch back to Session A
    switched = chat.switch_session(sid_a)
    assert switched is True
    assert chat.state.session_id == sid_a
    assert len(chat.get_history()) == 2
    assert chat.get_history()[0].content == "Query A1"

    # Switch back to Session B
    switched_b = chat.switch_session(sid_b)
    assert switched_b is True
    assert chat.state.session_id == sid_b
    assert len(chat.get_history()) == 1
    assert chat.get_history()[0].content == "Query B1"


def test_chat_session_memory_pruning_and_summarization(
    test_workspace: WorkspaceBoundary,
) -> None:
    """Verify exceeding max_memory_messages automatically condenses and prunes history."""
    settings = Settings.load(workspace=test_workspace.root)

    # Configure small memory window of 4 messages
    chat = ChatSession(
        workspace=test_workspace,
        settings=settings,
        max_memory_messages=4,
    )

    # Add 6 message turns (3 pairs)
    chat.add_user_message("Turn 1: Project init")
    chat.add_assistant_message("Turn 1: Ack init")
    chat.add_user_message("Turn 2: Setup database")
    chat.add_assistant_message("Turn 2: Ack database")
    chat.add_user_message("Turn 3: Write tests")
    chat.add_assistant_message("Turn 3: Ack tests")

    # In-memory and active DB messages should be pruned to keep last 4
    assert len(chat.get_history()) == 4
    assert chat.get_history()[0].content == "Turn 2: Setup database"
    assert chat.get_history()[-1].content == "Turn 3: Ack tests"

    # Summary should be generated and stored
    summary = chat.get_session_summary()
    assert summary is not None
    assert "Condensed memory" in summary


def test_chat_session_secret_redaction(test_workspace: WorkspaceBoundary) -> None:
    """Verify secrets in user and assistant messages are redacted in memory and database."""
    settings = Settings.load(workspace=test_workspace.root)
    chat = ChatSession(workspace=test_workspace, settings=settings)

    raw_user_msg = "Use token sk-proj-1234567890abcdef1234567890abcdef for auth"
    record = chat.add_user_message(raw_user_msg)

    assert "sk-proj" not in record.content
    assert "[REDACTED]" in record.content

    # Also check SQLite DB
    db_msgs = chat.db.get_messages(chat.state.session_id)
    assert "sk-proj" not in db_msgs[0]["content"]
    assert "[REDACTED]" in db_msgs[0]["content"]


# ---------------------------------------------------------------------------
# REPL In-Session Commands Tests
# ---------------------------------------------------------------------------


def test_chat_repl_session_commands(test_workspace: WorkspaceBoundary) -> None:
    """Verify /session list, /session switch, /session new, and /session info in REPL."""
    output_buffer = io.StringIO()
    test_console = Console(file=output_buffer, force_terminal=False, width=120)
    settings = Settings.load(workspace=test_workspace.root)

    session = ChatSession(workspace=test_workspace, settings=settings)
    engine = ChatEngine(session=session, boundary=test_workspace)
    repl = ChatREPL(engine=engine, console=test_console, enable_streaming_animation=False)

    # /session list
    assert repl.handle_command("/session list") is True
    assert "Persistent Conversation Sessions" in output_buffer.getvalue()

    # /session info
    assert repl.handle_command("/session info") is True
    assert f"Session Info: {session.state.session_id}" in output_buffer.getvalue()

    # /session new CustomName
    assert repl.handle_command("/session new RefactoringWorkflow") is True
    assert "Created and activated session:" in output_buffer.getvalue()
    new_sid = session.state.session_id
    assert new_sid.startswith("sess_")

    # Add message in new session
    session.add_user_message("Refactor toolbridge module")

    # /session switch back to original
    first_sid = session.db.list_sessions()[-1]["id"]
    assert repl.handle_command(f"/session switch {first_sid}") is True
    assert session.state.session_id == first_sid

    # /session switch to invalid
    assert repl.handle_command("/session switch non_existent_sid") is True
    assert "Session not found" in output_buffer.getvalue()


# ---------------------------------------------------------------------------
# Typer CLI Commands Tests
# ---------------------------------------------------------------------------


def test_cli_session_subcommands(tmp_path: Path) -> None:
    """Verify termicode session new, list, switch, delete CLI commands."""
    runner = CliRunner()
    ws_arg = ["-w", str(tmp_path)]

    # 1. Initialize workspace
    init_res = runner.invoke(app, [*ws_arg, "init"])
    assert init_res.exit_code == 0

    # 2. session new
    new_res = runner.invoke(app, [*ws_arg, "session", "new", "--title", "CLI Test Session"])
    assert new_res.exit_code == 0
    assert "Created new session:" in new_res.output
    # Extract session ID from output
    created_sid = ""
    for word in new_res.output.split():
        if word.startswith("sess_"):
            created_sid = word
            break
    assert created_sid != ""

    # 3. session list
    list_res = runner.invoke(app, [*ws_arg, "session", "list"])
    assert list_res.exit_code == 0
    assert created_sid in list_res.output
    assert "CLI Test" in list_res.output

    # 4. Create second session
    new2_res = runner.invoke(app, [*ws_arg, "session", "new", "--title", "Second CLI Session"])
    assert new2_res.exit_code == 0
    sid2 = ""
    for word in new2_res.output.split():
        if word.startswith("sess_"):
            sid2 = word
            break
    assert sid2 != ""

    # 5. session switch
    switch_res = runner.invoke(app, [*ws_arg, "session", "switch", created_sid])
    assert switch_res.exit_code == 0
    assert f"Active session switched to: {created_sid}" in switch_res.output

    # Switch to invalid ID
    switch_fail = runner.invoke(app, [*ws_arg, "session", "switch", "sess_doesnotexist"])
    assert switch_fail.exit_code == 1

    # 6. session delete
    del_res = runner.invoke(app, [*ws_arg, "session", "delete", sid2])
    assert del_res.exit_code == 0
    assert f"Deleted session: {sid2}" in del_res.output

    # Delete invalid ID
    del_fail = runner.invoke(app, [*ws_arg, "session", "delete", "sess_doesnotexist"])
    assert del_fail.exit_code == 1


def test_cli_chat_with_session_flag(tmp_path: Path) -> None:
    """Verify 'termicode chat --offline --session <id>' resumes the given session."""
    runner = CliRunner()
    ws_arg = ["-w", str(tmp_path)]

    # Initialize workspace
    runner.invoke(app, [*ws_arg, "init"])

    # Create explicit session via CLI
    new_res = runner.invoke(app, [*ws_arg, "session", "new", "--title", "Chat Flag Test"])
    created_sid = ""
    for word in new_res.output.split():
        if word.startswith("sess_"):
            created_sid = word
            break
    assert created_sid != ""

    # Launch chat with --session <created_sid> and immediately exit
    chat_res = runner.invoke(
        app,
        [*ws_arg, "chat", "--offline", "--session", created_sid],
        input="exit\n",
    )
    assert chat_res.exit_code == 0
    assert created_sid in chat_res.output
    assert "Interactive Chat Session" in chat_res.output
