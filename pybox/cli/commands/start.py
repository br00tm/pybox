"""pybox start — start one or more stopped containers."""

from __future__ import annotations

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
    detach: Annotated[
        bool,
        typer.Option("--detach", "-d", help="Start in background (do not wait for exit)"),
    ] = False,
) -> None:
    """Start stopped CONTAINER_IDS.

    Containers must be in 'created' or 'stopped' state.
    Use --detach / -d to start in background and return immediately.
    """
    import os
    import time

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
            manager.start(cid, detach=detach)
        except PyBoxError as exc:
            print_error(str(exc))
            had_error = True
            continue

        label = ref  # keep name or partial ID as shown by user
        print_success(f"Started {label}")

        if not detach:
            # Foreground: wait for this container before starting the next
            try:
                manager.wait(cid)
            except PyBoxError as exc:
                print_error(f"Wait failed for '{label}': {exc}")
                had_error = True
        else:
            # Detached: fork a background watcher and return immediately
            pid_b = os.fork()
            if pid_b > 0:
                continue  # parent: move on to next container
            # Background watcher: poll until the container process exits
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
