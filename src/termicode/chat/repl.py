"""Interactive terminal REPL chat interface for TermiCode AI using Rich and Typer."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TextIO

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from termicode.chat.engine import ChatEngine
from termicode.chat.session import DocumentNotFoundError
from termicode.console import console as default_console
from termicode.console import print_error
from termicode.version_control import CheckpointManager, is_git_repo


class ChatREPL:
    """Rich interactive prompt REPL for conversation, file management, and tool inspection."""

    def __init__(
        self,
        engine: ChatEngine,
        console: Console | None = None,
        input_stream: TextIO | None = None,
        enable_streaming_animation: bool = True,
    ) -> None:
        self.engine = engine
        self.session = engine.session
        self.console = console or default_console
        self.input_stream = input_stream or sys.stdin
        self.enable_streaming_animation = enable_streaming_animation

    def render_welcome_banner(self) -> None:
        """Render the welcome header with workspace info and shortcut instructions."""
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("Property", style="bold cyan")
        table.add_column("Value", style="bright_white")

        table.add_row("Session ID", f"[bold yellow]{self.session.state.session_id}[/bold yellow]")
        table.add_row("Workspace", str(self.engine.boundary.root))
        table.add_row("Provider", f"{self.session.state.provider} ({self.session.state.model})")
        tb_status = (
            "[bold green]Enabled[/bold green]"
            if self.session.state.toolbridge_enabled
            else "[dim]Disabled[/dim]"
        )
        table.add_row("ToolBridge Gateway", tb_status)
        step_status = (
            "[bold green]ON[/bold green]" if self.session.state.verbose_steps else "[dim]OFF[/dim]"
        )
        table.add_row("Intermediate Steps", step_status)

        sub = (
            "[dim]Type [bold white]/help[/bold white] for commands | "
            "[bold white]/session list[/bold white] for persistent memory | "
            "[bold white]/upload <path>[/bold white] to attach files | "
            "[bold white]exit[/bold white] to quit[/dim]"
        )
        panel = Panel(
            table,
            title="[bold blue]TermiCode AI -- Interactive Chat Session[/bold blue]",
            subtitle=sub,
            border_style="blue",
            padding=(1, 2),
        )
        self.console.print(panel)

    def render_help(self) -> None:
        """Render formatted help manual for chat mode."""
        table = Table(title="TermiCode Chatbot -- Commands & Controls", border_style="cyan")
        table.add_column("Command / Shortcut", style="bold cyan", width=24)
        table.add_column("Description", style="white")

        table.add_row("/help, help", "Display this commands and navigation guide.")
        table.add_row("/new, /clear", "Clear conversation message history and start a fresh turn.")
        table.add_row("/history", "View all previous message turns in this session.")
        table.add_row(
            "/session list", "List all persistent conversation sessions in this workspace."
        )
        table.add_row(
            "/session switch <id>", "Switch to another session ID and restore conversation."
        )
        table.add_row("/session new [title]", "Create and activate a new persistent session.")
        table.add_row("/session info", "Display current session metadata, message count & summary.")
        table.add_row(
            "/upload <path>", "Attach a file, notes, or PDF document into the chat session."
        )
        table.add_row("/docs, /files", "List all currently attached session documents.")
        table.add_row("/drop <id_or_name>", "Remove an attached document from the session.")
        table.add_row("/steps [on|off]", "Toggle intermediate planner, safety, and tool step logs.")
        table.add_row(
            "/settings [k=v]", "View or update settings (model, provider, toolbridge, steps)."
        )
        table.add_row(
            "/mcp <list|call|...>", "Discover and execute remote ToolBridge MCP Gateway tools."
        )
        table.add_row(
            "/checkpoint [msg]", "Create a Git checkpoint saving current workspace state."
        )
        table.add_row("/rollback [id]", "Revert workspace to last checkpoint (or specified ID).")
        table.add_row("/checkpoints", "List recent Git checkpoints recorded for this workspace.")
        table.add_row("/multiline", "Enter multi-line input mode (end with an empty line).")
        table.add_row("/exit, /quit, exit", "Exit the chat session.")

        self.console.print(table)

    def render_history(self) -> None:
        """Render chronological conversation history."""
        messages = self.session.get_history()
        if not messages:
            self.console.print("[dim]No messages in active conversation history.[/dim]")
            return

        table = Table(title="Conversation History", border_style="blue")
        table.add_column("#", style="dim", width=4)
        table.add_column("Time", style="cyan", width=10)
        table.add_column("Role", style="bold", width=12)
        table.add_column("Message Preview", style="white")

        for idx, m in enumerate(messages, start=1):
            role_style = "bold green" if m.role == "assistant" else "bold cyan"
            preview = m.content.strip().split("\n")[0][:90]
            if len(m.content.strip().split("\n")[0]) > 90:
                preview += "..."
            table.add_row(
                str(idx), m.timestamp, f"[{role_style}]{m.role.upper()}[/{role_style}]", preview
            )

        self.console.print(table)

    def render_documents(self) -> None:
        """Render attached session documents."""
        docs = self.session.list_documents()
        if not docs:
            msg = (
                "[dim]No documents attached. "
                "Use [bold white]/upload <path>[/bold white] to attach files.[/dim]"
            )
            self.console.print(msg)
            return

        table = Table(title="Attached Session Documents", border_style="magenta")
        table.add_column("Doc ID", style="bold magenta", width=8)
        table.add_column("Filename", style="bold white", width=24)
        table.add_column("Size", style="cyan", width=12)
        table.add_column("Type", style="dim", width=8)
        table.add_column("Path", style="dim")

        for d in docs:
            size_str = f"{d.size_bytes:,} bytes"
            if d.size_bytes > 1048576:
                size_str = f"{d.size_bytes / 1048576:.2f} MB"
            elif d.size_bytes > 1024:
                size_str = f"{d.size_bytes / 1024:.1f} KB"

            table.add_row(d.doc_id, d.filename, size_str, d.file_type, d.path)

        self.console.print(table)

    def render_settings(self, arg: str = "") -> None:
        """Display or update session settings."""
        if not arg:
            table = Table(title="Session Configuration", border_style="yellow")
            table.add_column("Setting", style="bold yellow", width=22)
            table.add_column("Current Value", style="white")

            table.add_row("provider", self.session.state.provider)
            table.add_row("model", self.session.state.model)
            table.add_row("toolbridge_enabled", str(self.session.state.toolbridge_enabled))
            table.add_row("toolbridge_url", self.session.state.toolbridge_url)
            table.add_row("verbose_steps", str(self.session.state.verbose_steps))

            self.console.print(table)
            self.console.print(
                "[dim]Update settings with: [bold white]/settings key=value[/bold white][/dim]"
            )
            return

        parts = arg.split("=", 1)
        if len(parts) != 2:
            self.console.print(
                "[bold red]Format error:[/bold red] Use `/settings key=value` (e.g. `model=gpt-4o`)"
            )
            return

        ok, msg = self.session.update_setting(parts[0], parts[1])
        if ok:
            self.console.print(f"[bold green][OK][/bold green] {msg}")
        else:
            self.console.print(f"[bold red][ERROR][/bold red] {msg}")

    def render_sessions(self) -> None:
        """Render persistent conversation sessions from database."""
        sessions = self.session.db.list_sessions()
        if not sessions:
            self.console.print("[dim]No persistent sessions found in database.[/dim]")
            return

        table = Table(title="Persistent Conversation Sessions", border_style="cyan")
        table.add_column("Session ID", style="bold cyan", width=14)
        table.add_column("Title", style="white")
        table.add_column("Msgs", justify="right", style="magenta", width=6)
        table.add_column("Active", justify="center", style="green", width=8)
        table.add_column("Updated", style="dim", width=20)

        for s in sessions:
            active_marker = "[bold green]YES[/bold green]" if s["is_active"] else "[dim]NO[/dim]"
            updated = s["updated_at"][:19].replace("T", " ")
            table.add_row(
                s["id"],
                s["title"],
                str(s["message_count"]),
                active_marker,
                updated,
            )

        self.console.print(table)

    def render_session_info(self) -> None:
        """Render detailed status and summary of current active session."""
        sess = self.session.db.get_session(self.session.state.session_id)
        if not sess:
            self.console.print(
                f"[bold yellow]Current session '{self.session.state.session_id}' "
                "not found in database.[/bold yellow]"
            )
            return

        table = Table(title=f"Session Info: {sess['id']}", border_style="yellow")
        table.add_column("Property", style="bold yellow", width=18)
        table.add_column("Value", style="white")

        table.add_row("Session ID", sess["id"])
        table.add_row("Title", sess["title"])
        table.add_row("Messages", str(sess["message_count"]))
        table.add_row("Created", sess["created_at"][:19].replace("T", " "))
        table.add_row("Updated", sess["updated_at"][:19].replace("T", " "))
        if sess["summary"]:
            table.add_row("Memory Summary", sess["summary"])

        self.console.print(table)

    def handle_mcp_command(self, arg: str) -> None:
        """Handle /mcp subcommands for ToolBridge MCP inspection and invocation."""
        from termicode.tools.toolbridge import ToolBridgeServerManager

        manager = ToolBridgeServerManager(self.session.workspace.root)
        sub_parts = arg.split(maxsplit=1)
        sub_cmd = sub_parts[0].lower() if sub_parts else "list"
        sub_arg = sub_parts[1].strip() if len(sub_parts) > 1 else ""

        if sub_cmd in ("servers", "server"):
            servers = manager.list_servers()
            if not servers:
                self.console.print("[dim]No ToolBridge servers configured.[/dim]")
                return
            table = Table(title="ToolBridge Servers", border_style="cyan")
            table.add_column("Server", style="bold cyan")
            table.add_column("URL", style="white")
            table.add_column("Auth", justify="center")
            table.add_column("Status", style="green")
            for s in servers:
                s_name = s.get("name", "unknown")
                s_url = s.get("url", "")
                has_auth = "[green]Token[/green]" if s.get("token") else "[dim]None[/dim]"
                client = manager.get_client(s_name)
                try:
                    health = client.check_health()
                    status_str = f"[bold green]Healthy ({health.get('status', 'OK')})[/bold green]"
                except Exception:
                    status_str = "[bold red]Unreachable[/bold red]"
                table.add_row(s_name, s_url, has_auth, status_str)
            self.console.print(table)
            return

        if sub_cmd in ("list", "ls", "tools"):
            target_server = sub_arg if sub_arg else None
            tools = manager.discover_tools(server_name=target_server)
            if not tools:
                suffix = f" from server '{target_server}'" if target_server else ""
                self.console.print(f"[yellow]No tools discovered{suffix}.[/yellow]")
                return
            table = Table(title="Discovered ToolBridge MCP Tools", border_style="magenta")
            table.add_column("Tool Name", style="bold cyan", width=28)
            table.add_column("Server", style="yellow", width=12)
            table.add_column("Tier", justify="center", style="magenta", width=10)
            table.add_column("Description", style="white")
            for t in tools:
                t_name = t.get("namespaced_name") or t.get("name") or "unknown"
                s_name = t.get("server_name", target_server or "default")
                tier = t.get("sensitivity_tier", "SAFE")
                desc = t.get("description", "")
                table.add_row(t_name, s_name, tier, desc)
            self.console.print(table)
            return

        if sub_cmd == "inspect":
            if not sub_arg:
                self.console.print(
                    "[bold red]Usage:[/bold red] `/mcp inspect <tool_name> [server]`"
                )
                return
            parts = sub_arg.split(maxsplit=1)
            t_name = parts[0]
            s_name = parts[1].strip() if len(parts) > 1 else None
            spec = manager.inspect_tool(t_name, server_name=s_name)
            if not spec:
                self.console.print(f"[bold red]Tool '{t_name}' not found.[/bold red]")
                return
            schema_json = json.dumps(spec.get("input_schema", {}), indent=2)
            panel_text = (
                f"[bold cyan]Tool:[/bold cyan] {t_name}\n"
                f"[bold cyan]Server:[/bold cyan] {spec.get('server_name', 'default')}\n"
                f"[bold cyan]Tier:[/bold cyan] {spec.get('sensitivity_tier', 'SAFE')}\n"
                f"[bold cyan]Description:[/bold cyan] {spec.get('description', '')}\n\n"
                f"[bold yellow]Input Schema:[/bold yellow]\n{schema_json}"
            )
            self.console.print(Panel(panel_text, title=f"MCP Tool: {t_name}", border_style="cyan"))
            return

        if sub_cmd == "call":
            parts = sub_arg.split(maxsplit=2)
            if len(parts) < 2:
                self.console.print(
                    "[bold red]Usage:[/bold red] `/mcp call <server> <tool_name> [args_json]`"
                )
                return
            s_name = parts[0]
            t_name = parts[1]
            raw_json = parts[2] if len(parts) > 2 else "{}"
            if raw_json.startswith("--json "):
                raw_json = raw_json[7:].strip()

            try:
                parsed_args = json.loads(raw_json) if raw_json else {}
                if not isinstance(parsed_args, dict):
                    raise ValueError("Arguments must be a JSON object.")
            except Exception as e:
                self.console.print(f"[bold red]Invalid JSON arguments:[/bold red] {e}")
                return

            preview = (
                f"[bold cyan]Server:[/bold cyan] {s_name}\n"
                f"[bold cyan]Tool:[/bold cyan] {t_name}\n"
                f"[bold cyan]Arguments:[/bold cyan]\n{json.dumps(parsed_args, indent=2)}"
            )
            self.console.print(
                Panel(
                    preview,
                    title="[bold yellow]MCP Tool Approval Gate[/bold yellow]",
                    border_style="yellow",
                )
            )
            self.console.print(
                "[bold yellow]Approve execution of this remote tool? (y/N): [/bold yellow]",
                end="",
            )
            confirm = self.input_stream.readline().strip().lower()
            if confirm not in ("y", "yes"):
                self.console.print("[yellow]Execution cancelled by user.[/yellow]")
                return

            try:
                result = manager.call_tool(s_name, t_name, parsed_args)
                self.console.print("[bold green][OK] Tool Execution Succeeded:[/bold green]")
                if isinstance(result, dict) and "result" in result:
                    self.console.print(result["result"])
                else:
                    self.console.print(json.dumps(result, indent=2))
            except Exception as err:
                msg = f"[bold red][ERROR] ToolBridge invocation failed:[/bold red] {err}"
                self.console.print(msg)
            return

        self.console.print(
            f"[bold yellow]Unknown /mcp command:[/bold yellow] '{sub_cmd}'. "
            "Available: `/mcp list [server]`, `/mcp servers`, `/mcp inspect <tool>`, "
            "`/mcp call <server> <tool> [args]`."
        )

    def render_step_logs(self, steps: list[dict[str, str]]) -> None:
        """Render intermediate planner and safety verification steps."""
        if not steps or not self.session.state.verbose_steps:
            return

        table = Table(show_header=True, box=None, padding=(0, 1))
        table.add_column("Phase", style="bold cyan", width=12)
        table.add_column("Action", style="bold yellow", width=24)
        table.add_column("Detail", style="dim white")

        for s in steps:
            table.add_row(s.get("phase", ""), s.get("action", ""), s.get("detail", ""))

        panel = Panel(
            table,
            title="[bold dim blue]Intermediate Planning & Safety Steps[/bold dim blue]",
            border_style="dim blue",
            padding=(0, 1),
        )
        self.console.print(panel)

    def read_multiline_input(self) -> str:
        """Read multiple lines from input until an empty line or /end is entered."""
        self.console.print(
            "[dim cyan]Entering multi-line mode. Enter empty line or '/end' to submit:[/dim cyan]"
        )
        lines: list[str] = []
        while True:
            self.console.print("[dim cyan]... [/dim cyan]", end="")
            try:
                line = self.input_stream.readline()
                if not line:
                    break
                stripped = line.rstrip("\r\n")
                if stripped in ("/end", '"""') or (not stripped and lines):
                    break
                lines.append(stripped)
            except (EOFError, KeyboardInterrupt):
                break
        return "\n".join(lines).strip()

    def handle_command(self, raw_line: str) -> bool:
        """Parse and execute slash commands or keywords.

        Returns:
            True if chat session should continue; False if exit requested.
        """
        line = raw_line.strip()
        parts = line.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("/exit", "/quit", "exit", "quit"):
            self.console.print("\n[bold cyan]Goodbye! TermiCode chat session ended.[/bold cyan]")
            return False

        if cmd in ("/help", "help"):
            self.render_help()
            return True

        if cmd in ("/new", "/clear"):
            self.session.new_chat()
            self.console.print(
                "[bold green][OK][/bold green] Started new conversation. History cleared."
            )
            return True

        if cmd == "/history":
            self.render_history()
            return True

        if cmd == "/session":
            sub_parts = arg.split(maxsplit=1)
            sub_cmd = sub_parts[0].lower() if sub_parts else "list"
            sub_arg = sub_parts[1].strip() if len(sub_parts) > 1 else ""

            if sub_cmd in ("list", "ls"):
                self.render_sessions()
                return True

            if sub_cmd in ("switch", "select"):
                if not sub_arg:
                    self.console.print("[bold red]Usage:[/bold red] `/session switch <session_id>`")
                    return True
                if self.session.switch_session(sub_arg):
                    self.console.print(
                        f"[bold green][OK] Switched to session:[/bold green] '{sub_arg}'"
                    )
                else:
                    self.console.print(
                        f"[bold red][ERROR] Session not found:[/bold red] '{sub_arg}'"
                    )
                return True

            if sub_cmd == "new":
                title = sub_arg or "New Session"
                new_id = self.session.new_chat(title=title)
                self.console.print(
                    f"[bold green][OK] Created and activated session:[/bold green] '{new_id}'"
                )
                return True

            if sub_cmd == "info":
                self.render_session_info()
                return True

            self.console.print(
                f"[bold yellow]Unknown session command:[/bold yellow] '{sub_cmd}'. "
                "Available: `/session list`, `/session switch <id>`, "
                "`/session new [title]`, `/session info`."
            )
            return True

        if cmd in ("/docs", "/files"):
            self.render_documents()
            return True

        if cmd in ("/upload", "/attach"):
            if not arg:
                self.console.print(
                    "[bold red]Usage:[/bold red] `/upload <file_path>` (e.g. `/upload notes.md`)"
                )
                return True
            try:
                fname = Path(arg).name
                with self.console.status(
                    f"[bold cyan]Reading & indexing '{fname}'...[/bold cyan]",
                    spinner="dots",
                ):
                    doc = self.session.add_document(arg)
                msg = (
                    f"[bold green][OK] Attached:[/bold green] {doc.filename} "
                    f"({doc.doc_id}, {doc.size_bytes} bytes)"
                )
                self.console.print(msg)
            except DocumentNotFoundError as err:
                self.console.print(f"[bold red][ERROR][/bold red] {err}")
            except Exception as err:
                print_error(
                    err,
                    title="Document Upload Error",
                    workspace=getattr(self.engine.boundary, "root", None),
                )
            return True

        if cmd in ("/drop", "/remove"):
            if not arg:
                self.console.print("[bold red]Usage:[/bold red] `/drop <doc_id_or_filename>`")
                return True

            parts = arg.split()
            force = False
            clean_parts = []
            for p in parts:
                if p in ("-y", "--yes", "--force", "-f"):
                    force = True
                else:
                    clean_parts.append(p)
            target = " ".join(clean_parts).strip()
            if not target:
                self.console.print("[bold red]Usage:[/bold red] `/drop <doc_id_or_filename>`")
                return True

            # Match target against attached documents
            docs = self.session.list_documents()
            matching_doc = None
            for d in docs:
                if d.doc_id == target or d.filename == target or Path(d.path).name == target:
                    matching_doc = d
                    break

            if matching_doc is None:
                self.console.print(
                    f"[bold yellow][WARN] Document not found:[/bold yellow] '{target}'"
                )
                return True

            # Prompt confirmation in interactive mode
            if not force and hasattr(self.input_stream, "isatty") and self.input_stream.isatty():
                prompt_msg = (
                    f"[bold yellow]Are you sure you want to remove document "
                    f"'{matching_doc.filename}'? (y/N): [/bold yellow]"
                )
                self.console.print(prompt_msg, end="")
                confirm = self.input_stream.readline().strip().lower()
                if confirm not in ("y", "yes"):
                    self.console.print("[yellow]Document removal cancelled.[/yellow]")
                    return True

            if self.session.remove_document(target):
                self.console.print(
                    f"[bold green][OK] Removed document matching:[/bold green] "
                    f"'{matching_doc.filename}'"
                )
            else:
                self.console.print(
                    f"[bold yellow][WARN] Document not found:[/bold yellow] '{target}'"
                )
            return True

        if cmd == "/steps":
            if arg:
                is_on = arg.lower() in ("on", "true", "1")
                self.session.toggle_steps(is_on)
            else:
                self.session.toggle_steps()
            state_str = "ON" if self.session.state.verbose_steps else "OFF"
            msg = f"[bold cyan][STEPS][/bold cyan] Steps: [bold white]{state_str}[/bold white]"
            self.console.print(msg)
            return True

        if cmd == "/settings":
            self.render_settings(arg)
            return True

        if cmd == "/mcp":
            self.handle_mcp_command(arg)
            return True

        if cmd == "/checkpoint":
            ws_root = getattr(self.engine.boundary, "root", Path.cwd())
            if not is_git_repo(ws_root):
                self.console.print(
                    "[bold red][ERROR][/bold red] Workspace is not a Git repository. "
                    "Run 'termicode init'."
                )
                return True
            mgr = CheckpointManager(ws_root)
            cp = mgr.create_checkpoint(message=arg or None, checkpoint_type="manual")
            self.console.print(
                f"[bold green][OK] Created checkpoint:[/] [cyan]{cp.id}[/] ({cp.message})"
            )
            return True

        if cmd == "/rollback":
            ws_root = getattr(self.engine.boundary, "root", Path.cwd())
            if not is_git_repo(ws_root):
                self.console.print(
                    "[bold red][ERROR][/bold red] Workspace is not a Git repository. "
                    "Run 'termicode init'."
                )
                return True
            mgr = CheckpointManager(ws_root)
            parts = arg.split()
            force = False
            clean_parts = []
            for p in parts:
                if p in ("-y", "--yes", "--force", "-f"):
                    force = True
                else:
                    clean_parts.append(p)
            target_id = " ".join(clean_parts).strip()

            target_label = f"checkpoint '{target_id}'" if target_id else "last checkpoint"
            if not force and hasattr(self.input_stream, "isatty") and self.input_stream.isatty():
                self.console.print(
                    f"[bold yellow]Revert workspace changes to {target_label}? "
                    "Uncommitted modifications will be reset. (y/N): [/bold yellow]",
                    end="",
                )
                confirm = self.input_stream.readline().strip().lower()
                if confirm not in ("y", "yes"):
                    self.console.print("[yellow]Rollback cancelled by user.[/yellow]")
                    return True

            try:
                cp = mgr.rollback(target_id or None)
                self.console.print(
                    f"[bold green][OK] Restored workspace to checkpoint:[/] "
                    f"[cyan]{cp.id}[/] ({cp.message})"
                )
            except Exception as err:
                self.console.print(f"[bold red][ERROR] Rollback failed:[/] {err}")
            return True

        if cmd == "/checkpoints":
            ws_root = getattr(self.engine.boundary, "root", Path.cwd())
            if not is_git_repo(ws_root):
                self.console.print(
                    "[bold red][ERROR][/bold red] Workspace is not a Git repository. "
                    "Run 'termicode init'."
                )
                return True
            mgr = CheckpointManager(ws_root)
            cps = mgr.list_checkpoints(limit=10)
            if not cps:
                self.console.print("[dim]No checkpoints recorded for this workspace.[/dim]")
                return True
            table = Table(title="Git Checkpoints", border_style="cyan")
            table.add_column("ID", style="bold cyan")
            table.add_column("Type", style="magenta")
            table.add_column("Timestamp", style="dim")
            table.add_column("Message")
            for item in cps:
                table.add_row(
                    item.id,
                    item.type.upper(),
                    item.timestamp[:19].replace("T", " "),
                    item.message,
                )
            self.console.print(table)
            return True

        if cmd == "/multiline":
            multiline_text = self.read_multiline_input()
            if multiline_text:
                self.process_query(multiline_text)
            return True

        # Unrecognized slash command
        if cmd.startswith("/"):
            msg = (
                f"[bold yellow]Unknown command:[/bold yellow] '{cmd}'. "
                "Type [bold white]/help[/bold white] for available commands."
            )
            self.console.print(msg)
            return True

        # Standard conversational query
        self.process_query(line)
        return True

    def process_query(self, query: str) -> None:
        """Process conversational user query and display response with streaming."""
        self.console.print()

        try:
            with self.console.status(
                "[bold cyan]Thinking & generating response...[/bold cyan]",
                spinner="dots",
            ):
                response_text, step_logs = self.engine.generate_response(query)
        except Exception as err:
            ws_root = getattr(self.engine.boundary, "root", None)
            print_error(
                err,
                title="Query Generation Error",
                workspace=ws_root,
            )
            return

        # 1. Render intermediate planning steps
        self.render_step_logs(step_logs)

        # 2. Render response with streaming effect
        self.console.print("[bold green]TermiCode AI[/bold green]:")

        if self.enable_streaming_animation and self.console.is_terminal:
            accumulated = ""
            with Live(
                Markdown("", code_theme="monokai"),
                refresh_per_second=20,
                console=self.console,
            ) as live:
                for chunk in self.engine.stream_chunks(response_text, chunk_delay=0.008):
                    accumulated += chunk
                    live.update(Markdown(accumulated, code_theme="monokai"))
        else:
            self.console.print(Markdown(response_text, code_theme="monokai"))

        self.console.print()

    def run_loop(self) -> None:
        """Main interactive REPL execution loop."""
        self.render_welcome_banner()

        while True:
            try:
                self.console.print(
                    "[bold cyan]termicode[/bold cyan] [bold white]>[/bold white] ", end=""
                )
                line = self.input_stream.readline()

                if not line:  # EOF encountered
                    self.console.print("\n[bold cyan]EOF encountered. Goodbye![/bold cyan]")
                    break

                stripped = line.strip()
                if not stripped:
                    continue

                # Check if user entered triple quotes to initiate multi-line block
                if stripped.startswith('"""') and not (
                    stripped.endswith('"""') and len(stripped) > 3
                ):
                    initial = stripped.removeprefix('"""')
                    rest = self.read_multiline_input()
                    full_query = f"{initial}\n{rest}".strip()
                    if full_query:
                        self.process_query(full_query)
                    continue

                keep_running = self.handle_command(stripped)
                if not keep_running:
                    break

            except KeyboardInterrupt:
                self.console.print("\n[dim](Session interrupted. Type 'exit' to quit.)[/dim]")
                continue
            except Exception as err:
                ws_root = getattr(self.engine.boundary, "root", None)
                print_error(
                    err,
                    title="Unexpected Chat Error",
                    workspace=ws_root,
                )
