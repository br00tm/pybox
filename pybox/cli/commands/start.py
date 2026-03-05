"""pybox start — start one or more stopped containers in the background."""

from __future__ import annotations

import os
import time
from typing import Annotated

import typer

from pybox.cli.completion import complete_container
from pybox.cli.output import print_error, print_success

app = typer.Typer(help="Start one or more stopped containers.")


@app.callback(invoke_without_command=True)
def start(
    container_ids: Annotated[
        list[str],
        typer.Argument(
            help="Container ID(s) or name(s) to start",
            autocompletion=complete_container,
        ),
    ],
) -> None:
    """Start stopped CONTAINER_IDS in the background.

    Containers must be in 'created' or 'stopped' state.
    The container runs in the background — use 'pybox exec' to open a shell
    inside it, 'pybox logs' to see its output, and 'pybox stop' to stop it.
    """
    from pybox.config import get_config
    from pybox.container.runtime import ContainerManager
    from pybox.container.state import ContainerState
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

        try:
            manager.start(cid, detach=True)
        except PyBoxError as exc:
            print_error(str(exc))
            had_error = True
            continue

        print_success(f"Started {ref}")

        # Fork a background watcher that updates state when the container exits.
        # The CLI returns immediately; the watcher is reparented to init.
        pid_b = os.fork()
        if pid_b > 0:
            continue  # parent: move on to next container

        # Background watcher: redirect stdio, poll until container exits
        os.setsid()
        null_fd = os.open(os.devnull, os.O_RDWR)
        for fd in (0, 1, 2):
            os.dup2(null_fd, fd)
        os.close(null_fd)

        state = manager._state.get(cid)
        pid = state.get("pid")
        if pid:
            while True:
                try:
                    os.kill(pid, 0)
                    time.sleep(1)
                except (ProcessLookupError, PermissionError):
                    break
            manager._state.update(
                cid,
                state=ContainerState.STOPPED.value,
                pid=None,
                exit_code=0,
            )
        os._exit(0)  # noqa: SLF001

    if had_error:
        raise typer.Exit(1)
