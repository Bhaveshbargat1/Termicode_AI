"""User-journey integration tests verifying UX polish, error guidance, debug logs, and CLI help."""

from __future__ import annotations

import io
from pathlib import Path

from typer.testing import CliRunner

from termicode.chat.engine import ChatEngine
from termicode.chat.repl import ChatREPL
from termicode.chat.session import ChatSession
from termicode.cli import app
from termicode.config import Settings
from termicode.console import get_friendly_suggestion, log_exception, print_error
from termicode.providers.base import ProviderAuthError
from termicode.version_control import CheckpointManager, init_git_repo
from termicode.workspace import WorkspaceBoundary, WorkspaceError

runner = CliRunner()


def test_friendly_error_remedy_suggestions() -> None:
    """Verify error messages produce relevant and actionable remedy suggestions."""
    # 1. Auth error
    auth_err = ProviderAuthError("Incorrect API key provided: sk-invalid")
    sugg_auth = get_friendly_suggestion(str(auth_err), exc=auth_err)
    assert "OPENAI_API_KEY" in sugg_auth
    assert "--offline" in sugg_auth

    # 2. Workspace boundary error
    ws_err = WorkspaceError("Path 'C:/secret.txt' escapes approved workspace root")
    sugg_ws = get_friendly_suggestion(str(ws_err), exc=ws_err)
    assert "strictly confined" in sugg_ws

    # 3. Git repo missing error
    git_err = RuntimeError("Workspace is not a Git repository.")
    sugg_git = get_friendly_suggestion(str(git_err), exc=git_err)
    assert "termicode init" in sugg_git

    # 4. ToolBridge gateway connection error
    tb_err = ConnectionRefusedError("Failed to connect to ToolBridge gateway at http://127.0.0.1:8000")
    sugg_tb = get_friendly_suggestion(str(tb_err), exc=tb_err)
    assert "toolbridge.api.app" in sugg_tb

    # 5. Session not found error
    sess_err = ValueError("Session 'sess_ghost' not found in database.")
    sugg_sess = get_friendly_suggestion(str(sess_err), exc=sess_err)
    assert "termicode session list" in sugg_sess


def test_debug_log_file_persistence(tmp_path: Path) -> None:
    """Verify exceptions are logged to .termicode/logs/debug.log with timestamps and traces."""
    test_exc = ValueError("Simulated runtime calculation failure")
    log_path = log_exception(test_exc, context="Test Context", workspace=tmp_path)

    assert log_path.exists()
    assert log_path == tmp_path / ".termicode" / "logs" / "debug.log"

    content = log_path.read_text(encoding="utf-8")
    assert "CONTEXT: Test Context" in content
    assert "ERROR TYPE: ValueError" in content
    assert "Simulated runtime calculation failure" in content
    assert "TRACEBACK:" in content


def test_print_error_renders_panel_and_logs(tmp_path: Path) -> None:
    """Verify print_error renders panel, displays suggestion, and logs to file."""
    test_err = ProviderAuthError("Missing OpenAI authorization token")
    print_error(test_err, title="Auth Test", workspace=tmp_path)

    log_file = tmp_path / ".termicode" / "logs" / "debug.log"
    assert log_file.exists()
    log_text = log_file.read_text(encoding="utf-8")
    assert "ProviderAuthError" in log_text


def test_chat_repl_drop_document_with_interactive_confirmation(tmp_path: Path) -> None:
    """Verify /drop prompts for confirmation in interactive mode and respects Yes/No."""
    boundary = WorkspaceBoundary(tmp_path)
    boundary.ensure_termicode_dir()
    init_git_repo(tmp_path)

    doc_file = tmp_path / "architecture.md"
    doc_file.write_text("# High Level Design\nMicroservices with event sourcing.", encoding="utf-8")

    settings = Settings.load(workspace=tmp_path)
    session = ChatSession(workspace=boundary, settings=settings)
    engine = ChatEngine(session=session, boundary=boundary)

    # Attach document
    session.add_document("architecture.md")
    assert len(session.list_documents()) == 1

    # 1. User denies confirmation: input stream answers 'n\n'
    fake_stdin_no = io.StringIO("n\n")
    fake_stdin_no.isatty = lambda: True  # type: ignore[method-assign]
    repl_no = ChatREPL(engine=engine, input_stream=fake_stdin_no)

    assert repl_no.handle_command("/drop architecture.md") is True
    assert len(session.list_documents()) == 1  # Not dropped!

    # 2. User confirms: input stream answers 'y\n'
    fake_stdin_yes = io.StringIO("y\n")
    fake_stdin_yes.isatty = lambda: True  # type: ignore[method-assign]
    repl_yes = ChatREPL(engine=engine, input_stream=fake_stdin_yes)

    assert repl_yes.handle_command("/drop architecture.md") is True
    assert len(session.list_documents()) == 0  # Successfully dropped!

    # 3. Bypass confirmation with -y flag
    session.add_document("architecture.md")
    assert len(session.list_documents()) == 1
    fake_stdin_empty = io.StringIO("")
    fake_stdin_empty.isatty = lambda: True  # type: ignore[method-assign]
    repl_force = ChatREPL(engine=engine, input_stream=fake_stdin_empty)

    assert repl_force.handle_command("/drop -y architecture.md") is True
    assert len(session.list_documents()) == 0


def test_chat_repl_rollback_with_interactive_confirmation(tmp_path: Path) -> None:
    """Verify /rollback prompts confirmation in interactive mode and respects Yes/No."""
    boundary = WorkspaceBoundary(tmp_path)
    boundary.ensure_termicode_dir()
    init_git_repo(tmp_path)

    test_file = tmp_path / "code.py"
    test_file.write_text("x = 1\n", encoding="utf-8")

    mgr = CheckpointManager(tmp_path)
    cp = mgr.create_checkpoint("Initial state")

    test_file.write_text("x = 999\n", encoding="utf-8")

    settings = Settings.load(workspace=tmp_path)
    session = ChatSession(workspace=boundary, settings=settings)
    engine = ChatEngine(session=session, boundary=boundary)

    # 1. Deny rollback
    fake_stdin_no = io.StringIO("n\n")
    fake_stdin_no.isatty = lambda: True  # type: ignore[method-assign]
    repl_no = ChatREPL(engine=engine, input_stream=fake_stdin_no)

    assert repl_no.handle_command("/rollback") is True
    assert test_file.read_text(encoding="utf-8") == "x = 999\n"  # Unchanged!

    # 2. Confirm rollback
    fake_stdin_yes = io.StringIO("y\n")
    fake_stdin_yes.isatty = lambda: True  # type: ignore[method-assign]
    repl_yes = ChatREPL(engine=engine, input_stream=fake_stdin_yes)

    assert repl_yes.handle_command("/rollback") is True
    assert test_file.read_text(encoding="utf-8") == "x = 1\n"  # Restored!

    # 3. Rollback with -y flag
    test_file.write_text("x = 888\n", encoding="utf-8")
    fake_stdin_empty = io.StringIO("")
    fake_stdin_empty.isatty = lambda: True  # type: ignore[method-assign]
    repl_force = ChatREPL(engine=engine, input_stream=fake_stdin_empty)

    assert repl_force.handle_command(f"/rollback -y {cp.id}") is True
    assert test_file.read_text(encoding="utf-8") == "x = 1\n"


def test_chat_repl_process_query_with_spinner(tmp_path: Path) -> None:
    """Verify process_query executes smoothly with console status spinner."""
    boundary = WorkspaceBoundary(tmp_path)
    boundary.ensure_termicode_dir()
    init_git_repo(tmp_path)

    settings = Settings.load(workspace=tmp_path)
    session = ChatSession(workspace=boundary, settings=settings)
    engine = ChatEngine(session=session, boundary=boundary)

    repl = ChatREPL(engine=engine, enable_streaming_animation=False)
    # process_query should generate a response without unhandled exceptions
    repl.process_query("What files are here?")
    history = session.get_history()
    assert len(history) == 2
    assert history[0].role == "user"
    assert history[1].role == "assistant"


def test_session_delete_cli_with_force_flag(tmp_path: Path) -> None:
    """Verify termicode session delete deletes with --yes / --force flag."""
    boundary = WorkspaceBoundary(tmp_path)
    boundary.ensure_termicode_dir()
    init_git_repo(tmp_path)

    settings = Settings.load(workspace=tmp_path)
    session = ChatSession(workspace=boundary, settings=settings)
    sid = session.state.session_id

    ws_arg = ["--workspace", str(tmp_path)]
    res_del = runner.invoke(app, [*ws_arg, "session", "delete", sid, "--yes"])
    assert res_del.exit_code == 0
    assert "Deleted session:" in res_del.output


def test_cli_help_coverage_across_all_features() -> None:
    """Verify CLI help strings cover major commands, options, and descriptions."""
    # 1. Main app help
    res_main = runner.invoke(app, ["--help"])
    assert res_main.exit_code == 0
    expected_cmds = [
        "status",
        "run",
        "chat",
        "serve",
        "init",
        "checkpoint",
        "rollback",
        "checkpoints",
        "session",
        "toolbridge",
    ]
    for cmd in expected_cmds:
        assert cmd in res_main.output

    # 2. Run help
    res_run = runner.invoke(app, ["run", "--help"])
    assert res_run.exit_code == 0
    assert "--workflow" in res_run.output
    assert "--max-steps" in res_run.output
    assert "--offline" in res_run.output
    assert "--commit" in res_run.output

    # 3. Chat help
    res_chat = runner.invoke(app, ["chat", "--help"])
    assert res_chat.exit_code == 0
    assert "--offline" in res_chat.output
    assert "--session" in res_chat.output
    assert "--steps" in res_chat.output

    # 4. Checkpoint help
    res_cp = runner.invoke(app, ["checkpoint", "--help"])
    assert res_cp.exit_code == 0
    assert "checkpoint" in res_cp.output.lower()

    # 5. Rollback help
    res_rb = runner.invoke(app, ["rollback", "--help"])
    assert res_rb.exit_code == 0
    assert "--yes" in res_rb.output

    # 6. Session help
    res_sess = runner.invoke(app, ["session", "--help"])
    assert res_sess.exit_code == 0
    for sub in ["new", "list", "switch", "delete"]:
        assert sub in res_sess.output

    # 7. ToolBridge help
    res_tb = runner.invoke(app, ["toolbridge", "--help"])
    assert res_tb.exit_code == 0
    tb_subs = [
        "add-server",
        "list-servers",
        "remove-server",
        "list-tools",
        "inspect-tool",
        "call-tool",
    ]
    for sub in tb_subs:
        assert sub in res_tb.output


def test_full_user_journey_init_notes_checkpoint_rollback(tmp_path: Path) -> None:
    """End-to-end user journey: init -> note creation -> checkpoint -> mutation -> rollback."""
    ws_arg = ["--workspace", str(tmp_path)]

    # Step 1: Initialize workspace
    res_init = runner.invoke(app, [*ws_arg, "init"])
    assert res_init.exit_code == 0
    assert "TermiCode initialized" in res_init.output

    # Step 2: Create a note file
    note_path = tmp_path / "system_notes.md"
    note_path.write_text("# Core System\nDatabase port: 5432\nHost: db.local\n", encoding="utf-8")

    # Step 3: Create a checkpoint
    res_cp = runner.invoke(app, [*ws_arg, "checkpoint", "Before database update"])
    assert res_cp.exit_code == 0
    assert "Created checkpoint:" in res_cp.output

    # Step 4: Mutate file
    note_path.write_text("CORRUPTED CONTENT THAT WILL BE REVERTED\n", encoding="utf-8")
    assert "CORRUPTED" in note_path.read_text(encoding="utf-8")

    # Step 5: Rollback
    res_rb = runner.invoke(app, [*ws_arg, "rollback", "-y"])
    assert res_rb.exit_code == 0
    assert "Workspace successfully rolled back to checkpoint:" in res_rb.output

    # Step 6: Verify file is restored
    restored_text = note_path.read_text(encoding="utf-8")
    assert "Database port: 5432" in restored_text
    assert "CORRUPTED" not in restored_text
