"""Rich terminal output formatting, user-friendly error guidance, and status displays."""

from __future__ import annotations

import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from termicode import __version__
from termicode.config import Settings
from termicode.workspace import WorkspaceBoundary

console = Console()
error_console = Console(stderr=True)


def _resolve_log_dir(workspace: Path | None = None) -> Path:
    """Resolve the directory where debug logs should be written."""
    if workspace is not None:
        return workspace / ".termicode" / "logs"
    if Path(".termicode").is_dir():
        return Path(".termicode") / "logs"
    return Path.home() / ".termicode" / "logs"


def log_exception(
    exc: BaseException | str,
    context: str = "",
    workspace: Path | None = None,
) -> Path:
    """Append technical exception trace to .termicode/logs/debug.log."""
    log_dir = _resolve_log_dir(workspace)
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "debug.log"
        now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

        if isinstance(exc, BaseException):
            exc_type = type(exc).__name__
            exc_msg = str(exc)
            tb_str = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        else:
            exc_type = "ErrorMessage"
            exc_msg = str(exc)
            tb_str = (
                traceback.format_exc()
                if sys.exc_info()[0] is not None
                else "No active traceback available."
            )

        entry = (
            f"================================================================================\n"
            f"[{now_str}] CONTEXT: {context or 'TermiCode Execution'}\n"
            f"ERROR TYPE: {exc_type}\n"
            f"MESSAGE: {exc_msg}\n"
            f"TRACEBACK:\n{tb_str}\n"
            f"================================================================================\n\n"
        )
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(entry)
        return log_file
    except Exception:
        fallback = Path("termicode_debug.log")
        return fallback


def get_friendly_suggestion(err_str: str, exc: BaseException | None = None) -> str:
    """Determine an actionable suggestion or fix based on error message and type."""
    combined = f"{type(exc).__name__} {err_str}".lower() if exc else err_str.lower()

    if any(
        k in combined
        for k in (
            "api key",
            "openai_api_key",
            "providerautherror",
            "authenticationerror",
            "unauthorized",
        )
    ):
        return (
            "Ensure OPENAI_API_KEY is set in your .env or environment. "
            "To run without external keys, pass '--offline' or set TERMICODE_PROVIDER=fake."
        )

    if any(
        k in combined
        for k in (
            "not a git repository",
            "gitreponotfounderror",
            "gitcheckpointerror",
        )
    ):
        return (
            "Run 'termicode init' (or 'git init') to initialize Git "
            "version control and enable checkpoints."
        )

    if any(
        k in combined
        for k in (
            "toolbridge",
            "connection refused",
            "failed to connect",
            "unreachable",
        )
    ):
        return (
            "Ensure ToolBridge Gateway is running with: python -m toolbridge.api.app "
            "(default: http://127.0.0.1:8000), or check 'termicode toolbridge list-servers'."
        )

    if "session" in combined and "not found" in combined:
        return (
            "Run 'termicode session list' to view available sessions, "
            "or 'termicode session new' to create one."
        )

    if any(
        k in combined
        for k in (
            "workspace boundary",
            "containment",
            "workspaceerror",
            "outside approved",
        )
    ):
        return (
            "TermiCode is strictly confined to your workspace root. "
            "Ensure file paths are relative and inside workspace, or specify --workspace <path>."
        )

    if any(
        k in combined
        for k in (
            "file not found",
            "documentnotfounderror",
            "document not found",
            "filenotfounderror",
        )
    ):
        return "Check that the target file path exists inside your approved workspace directory."

    if "invalid json" in combined:
        return "Ensure your arguments are formatted as valid JSON (e.g. '{\"key\": \"value\"}')."

    if "timeout" in combined or "timed out" in combined:
        return (
            "The operation exceeded its timeout budget. You can increase "
            "'command_timeout_seconds' in .termicode/config.json."
        )

    return (
        "Check technical details in .termicode/logs/debug.log, "
        "or run with '--debug' for step-by-step traces."
    )


def print_status(
    settings: Settings,
    boundary: WorkspaceBoundary,
    provider_info: dict[str, str] | None = None,
) -> None:
    """Render a structured status panel showing workspace, provider, and configuration details."""
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Property", style="bold cyan")
    table.add_column("Value", style="bright_white", overflow="fold")

    table.add_row("Version", f"{__version__} (Production)")
    table.add_row("Workspace Root", str(boundary.root))
    table.add_row("Workspace Status", "[bold green]Valid & Contained[/bold green]")
    table.add_row("Metadata Directory", str(boundary.termicode_dir))
    table.add_row("Environment", settings.environment)
    table.add_row("Log Level", settings.log_level)

    # Provider & Model details
    if provider_info:
        table.add_row("Provider", provider_info.get("provider", settings.provider))
        table.add_row("Model", provider_info.get("model", settings.model))
        table.add_row("Endpoint", provider_info.get("endpoint", "Default"))
        status_val = provider_info.get("status", "Configured")
        is_ok = "Connected" in status_val or "Valid" in status_val
        style = "bold green" if is_ok else "bold yellow"
        table.add_row("Provider Status", f"[{style}]{status_val}[/{style}]")

    table.add_row("Max Steps Budget", str(settings.max_steps))
    table.add_row("Max Repairs Budget", str(settings.max_repairs))
    table.add_row("Command Timeout", f"{settings.command_timeout_seconds}s")
    table.add_row("Command Output Cap", f"{settings.max_command_output_bytes:,} bytes")
    table.add_row("Safety Policy", "[bold green]Deterministic Boundary Active[/bold green]")

    panel = Panel(
        table,
        title="[bold blue]TermiCode AI -- Runtime Status[/bold blue]",
        subtitle="[dim]Deterministic Safety Boundary - No Autonomous Shell[/dim]",
        border_style="blue",
        padding=(1, 2),
    )
    console.print(panel)


def print_error(
    message: str | BaseException,
    title: str = "TermiCode Security / Runtime Error",
    suggestion: str | None = None,
    log_tb: bool = True,
    workspace: Path | None = None,
    show_traceback: bool = False,
) -> None:
    """Render a user-friendly error message inside a red-bordered Rich panel.

    Automatically logs technical tracebacks to .termicode/logs/debug.log and
    provides actionable remedy suggestions without polluting the terminal.
    """
    exc: BaseException | None = (
        message if isinstance(message, BaseException) else sys.exc_info()[1]
    )
    raw_message = str(message)

    log_path: Path | None = None
    if log_tb:
        log_path = log_exception(exc or raw_message, context=title, workspace=workspace)

    effective_suggestion = suggestion or get_friendly_suggestion(raw_message, exc=exc)

    content = f"[bold red]{raw_message}[/bold red]"
    if effective_suggestion:
        content += f"\n\n[bold yellow]Help / Suggestion:[/] {effective_suggestion}"
    if log_path:
        content += f"\n[dim]Technical trace logged to: {log_path}[/dim]"

    panel = Panel(
        content,
        title=f"[bold bright_red]{title}[/bold bright_red]",
        border_style="red",
        padding=(0, 1),
    )
    error_console.print(panel)

    if show_traceback and exc is not None:
        error_console.print_exception(extra_lines=2)


def print_success(message: str) -> None:
    """Render a success message."""
    console.print(f"[bold green][OK][/bold green] {message}")


def print_warning(message: str) -> None:
    """Render a warning message."""
    console.print(f"[bold yellow][WARN][/bold yellow] {message}")


def print_info(message: str) -> None:
    """Render an informative message."""
    console.print(f"[bold cyan][INFO][/bold cyan] {message}")
