"""pybox exec — execute a command in a running container."""

from __future__ import annotations

from typing import Annotated

import typer

from pybox.cli.completion import complete_running_container
from pybox.cli.output import print_error

app = typer.Typer(help="Execute a command in a running container.")


@app.callback(invoke_without_command=True)
def exec_cmd(
    container_id: Annotated[str, typer.Argument(help="Container ID or name", autocompletion=complete_running_container)],
    cmd: Annotated[list[str], typer.Argument(help="Command to execute")],
    tty: Annotated[bool, typer.Option("--tty", "-t", help="Allocate a pseudo-TTY")] = False,
    interactive: Annotated[bool, typer.Option("--interactive", "-i", help="Keep stdin open")] = False,
) -> None:
    """Execute CMD inside CONTAINER_ID."""
    from pybox.config import get_config
    from pybox.container.exec import exec_in_container
    from pybox.exceptions import ContainerError, ContainerNotFoundError

    from pybox.container.runtime import ContainerManager
    from pybox.container.state import StateManager

    cfg = get_config()
    # Resolve name or partial ID → full container ID
    try:
        state_mgr = StateManager(cfg.containers_dir)
        resolved_id = state_mgr.resolve(container_id)
    except ContainerNotFoundError:
        print_error(f"Container '{container_id}' not found")
        raise typer.Exit(1)
    except ContainerError as exc:
        print_error(str(exc))
        raise typer.Exit(1)

    try:
        exit_code = exec_in_container(
            resolved_id,
            list(cmd),
            cfg.containers_dir,
            tty=tty or interactive,
        )
        raise typer.Exit(exit_code)
    except ContainerNotFoundError as exc:
        print_error(str(exc))
        raise typer.Exit(1)
    except ContainerError as exc:
        print_error(str(exc))
        raise typer.Exit(1)
