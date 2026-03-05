"""Rich-based output helpers for the PyBox CLI.

Provides:
    - print_table()            — render a generic Rich table
    - print_container_status() — container detail panel
    - print_error()            — red error message
    - print_success()          — green success message
    - LayerProgressBar         — context manager for layer download progress
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TransferSpeedColumn,
)
from rich.table import Table
from rich.text import Text

# Module-level console — shared by all output helpers
console = Console()
err_console = Console(stderr=True)


def print_table(
    headers: list[str],
    rows: list[list[str]],
    title: str | None = None,
) -> None:
    """Render a Rich table to stdout.

    Args:
        headers: Column header strings.
        rows:    List of rows; each row is a list of cell strings.
        title:   Optional table title displayed above the header.
    """
    table = Table(title=title, show_header=True, header_style="bold cyan")
    for header in headers:
        table.add_column(header)
    for row in rows:
        table.add_row(*row)
    console.print(table)


def print_container_status(container: dict[str, Any]) -> None:
    """Print a formatted container status panel.

    Args:
        container: State dict as returned by StateManager.get().
    """
    state = container.get("state", "unknown")
    state_color = {
        "running": "green",
        "stopped": "red",
        "created": "yellow",
        "paused": "blue",
        "removed": "dim",
    }.get(state, "white")

    lines = [
        f"[bold]ID:[/bold]      {container.get('id', 'n/a')}",
        f"[bold]Image:[/bold]   {container.get('image', 'n/a')}",
        f"[bold]State:[/bold]   [{state_color}]{state}[/{state_color}]",
        f"[bold]PID:[/bold]     {container.get('pid', 'n/a')}",
        f"[bold]Command:[/bold] {' '.join(container.get('cmd', []))}",
    ]
    if container.get("name"):
        lines.insert(1, f"[bold]Name:[/bold]    {container['name']}")

    console.print(Panel("\n".join(lines), title="Container", border_style="cyan"))


def print_error(message: str) -> None:
    """Print an error message to stderr in red.

    Args:
        message: Error description string.
    """
    err_console.print(f"[bold red]Error:[/bold red] {message}")


def print_success(message: str) -> None:
    """Print a success message to stdout in green.

    Args:
        message: Success description string.
    """
    console.print(f"[bold green]✓[/bold green] {message}")


@contextmanager
def LayerProgressBar(total_layers: int) -> Iterator[Progress]:
    """Context manager that shows a Rich progress bar for layer downloads.

    Usage:
        with LayerProgressBar(len(layers)) as progress:
            task = progress.add_task("Pulling layer...", total=layer_size)
            ...
            progress.advance(task, chunk_size)

    Args:
        total_layers: Number of layers to be displayed (informational).

    Yields:
        A rich.progress.Progress instance.
    """
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    )
    with progress:
        yield progress


def format_size(num_bytes: int) -> str:
    """Format a byte count into a human-readable string.

    Args:
        num_bytes: Size in bytes.

    Returns:
        Human-readable string, e.g. "12.3 MB".
    """
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes //= 1024
    return f"{num_bytes:.1f} PB"


def print_banner() -> None:
    """Print the PyBox ASCII art banner."""
    banner = Text(
        r"""
 ____        ____
|  _ \ _   _| __ )  _____  __
| |_) | | | |  _ \ / _ \ \/ /
|  __/| |_| | |_) | (_) >  <
|_|    \__, |____/ \___/_/\_\
       |___/
""",
        style="bold cyan",
    )
    console.print(banner)
    console.print(
        "[dim]A Python-native container runtime — containers, reimagined.[/dim]\n"
    )
