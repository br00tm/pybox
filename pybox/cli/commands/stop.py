"""pybox stop — stop one or more running containers."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console

from pybox.cli.completion import complete_running_container
from pybox.cli.output import print_error, print_success

app = typer.Typer(help="Stop one or more running containers.")
console = Console()


@app.callback(invoke_without_command=True)
def stop(
    container_ids: Annotated[
        list[str],
        typer.Argument(help="Container ID(s) or name(s) to stop", autocompletion=complete_running_container),
    ],
    timeout: Annotated[int, typer.Option("--timeout", "-t", help="Seconds before SIGKILL")] = 10,
) -> None:
    """Stop CONTAINER_IDS gracefully (SIGTERM, then SIGKILL after timeout)."""
    from pybox.config import get_config
    from pybox.container.runtime import ContainerManager
    from pybox.exceptions import ContainerNotFoundError, PyBoxError

    manager = ContainerManager(get_config())
    had_error = False

    for ref in container_ids:
        try:
            cid = manager._state.resolve(ref)
        except ContainerNotFoundError:
            print_error(f"Container '{ref}' not found")
            had_error = True
            continue
        except PyBoxError as exc:
            print_error(str(exc))
            had_error = True
            continue

        with console.status(f"Stopping {cid[:12]}..."):
            try:
                manager.stop(cid, timeout=timeout)
                print_success(f"Stopped {cid[:12]}")
            except PyBoxError as exc:
                print_error(str(exc))
                had_error = True

    if had_error:
        raise typer.Exit(1)
