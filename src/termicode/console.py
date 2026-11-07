"""Rich terminal output formatting and status displays."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from termicode.config import Settings
from termicode.workspace import WorkspaceBoundary

console = Console()
error_console = Console(stderr=True)


def print_status(
    settings: Settings,
    boundary: WorkspaceBoundary,
    provider_info: dict[str, str] | None = None,
) -> None:
    """Render a structured status panel showing workspace, provider, and configuration details."""
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Property", style="bold cyan")
    table.add_column("Value", style="bright_white", overflow="fold")

    table.add_row("Version", "0.1.0 (Phase 1 MVP)")
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


def print_error(message: str, title: str = "TermiCode Security / Runtime Error") -> None:
    """Render an error message inside a red-bordered Rich panel."""
    panel = Panel(
        f"[bold red]{message}[/bold red]",
        title=f"[bold bright_red]{title}[/bold bright_red]",
        border_style="red",
        padding=(0, 1),
    )
    error_console.print(panel)


def print_success(message: str) -> None:
    """Render a success message."""
    console.print(f"[bold green][OK][/bold green] {message}")


def print_warning(message: str) -> None:
    """Render a warning message."""
    console.print(f"[bold yellow][WARN][/bold yellow] {message}")


def print_info(message: str) -> None:
    """Render an informative message."""
    console.print(f"[bold cyan][INFO][/bold cyan] {message}")
