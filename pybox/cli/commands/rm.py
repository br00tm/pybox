"""pybox rm — remove one or more containers."""

from __future__ import annotations

from typing import Annotated

import typer

from pybox.cli.completion import complete_container
from pybox.cli.output import print_error, print_success

app = typer.Typer(help="Remove one or more containers.")


@app.callback(invoke_without_command=True)
def rm(
    container_ids: Annotated[
        list[str],
        typer.Argument(help="Container ID(s) or name(s) to remove", autocompletion=complete_container),
    ],
    force: Annotated[bool, typer.Option("--force", "-f", help="Stop and remove running containers")] = False,
) -> None:
    """Remove CONTAINER_IDS. Running containers require --force."""
    from pybox.config import get_config
    from pybox.container.runtime import ContainerManager
    from pybox.container.state import ContainerState
    from pybox.exceptions import ContainerError, ContainerNotFoundError

    manager = ContainerManager(get_config())
    had_error = False

    for ref in container_ids:
        try:
            cid = manager._state.resolve(ref)
            state = manager._state.get(cid)
            if state.get("state") == ContainerState.RUNNING.value:
                if not force:
                    print_error(
                        f"Container '{ref}' is running. Use --force to stop and remove."
                    )
                    had_error = True
                    continue
                manager.stop(cid)
            manager.remove(cid)
            print_success(f"Removed {cid[:12]}")
        except ContainerNotFoundError:
            print_error(f"Container '{ref}' not found")
            had_error = True
        except ContainerError as exc:
            print_error(str(exc))
            had_error = True

    if had_error:
        raise typer.Exit(1)
