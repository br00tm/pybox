"""pybox logs — stream container log output."""

from __future__ import annotations

import asyncio
import sys
from typing import Annotated, Optional

import typer

from pybox.cli.output import print_error

app = typer.Typer(help="Fetch container logs.")


@app.callback(invoke_without_command=True)
def logs(
    container_id: Annotated[str, typer.Argument(help="Container ID or name")],
    follow: Annotated[bool, typer.Option("--follow", "-f", help="Stream live log output")] = False,
    tail: Annotated[int, typer.Option("--tail", "-n", help="Number of lines to show")] = 100,
    timestamps: Annotated[bool, typer.Option("--timestamps", "-t", help="Show timestamps")] = False,
) -> None:
    """Fetch logs from a container."""
    from pybox.config import get_config
    from pybox.container.logs import LogManager
    from pybox.exceptions import ContainerNotFoundError

    cfg = get_config()
    log_mgr = LogManager(cfg.containers_dir)

    async def _stream() -> None:
        try:
            async for line in log_mgr.stream(container_id, follow=follow, tail=tail):
                if not timestamps:
                    # Strip the timestamp prefix (everything before the second space)
                    parts = line.split(" ", 2)
                    output = parts[2] if len(parts) == 3 else line
                else:
                    output = line
                sys.stdout.write(output)
                sys.stdout.flush()
        except ContainerNotFoundError:
            print_error(f"Container '{container_id}' not found")
            raise typer.Exit(1)
        except KeyboardInterrupt:
            pass

    try:
        asyncio.run(_stream())
    except KeyboardInterrupt:
        pass
