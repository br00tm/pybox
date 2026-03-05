"""pybox run — pull an image and start a container interactively or detached.

Usage:
    pybox run --image ubuntu:24.04 -- /bin/bash
    pybox run -i ubuntu:24.04 -m 256m --cpu 0.5 -e FOO=bar -- /bin/sh -c "echo hi"
    pybox run --image alpine:latest --rm -- /bin/echo hello
    pybox run --image ubuntu:24.04 --name webserver -d -- /bin/sleep 3600
"""

from __future__ import annotations

import asyncio
import os
from typing import Annotated, Optional

import typer

from pybox.cgroups.specs import CgroupSpec
from pybox.cli.completion import complete_image
from pybox.cli.output import print_error
from pybox.config import get_config
from pybox.container.runtime import ContainerManager
from pybox.exceptions import PyBoxError

app = typer.Typer(help="Run a container from an image.")


@app.callback(invoke_without_command=True)
def run(
    image: Annotated[str, typer.Option("--image", "-i", help="Image reference, e.g. ubuntu:24.04", autocompletion=complete_image)],
    cmd: Annotated[list[str], typer.Argument(help="Command to run inside the container")],
    name: Annotated[Optional[str], typer.Option("--name", "-n", help="Container name for easy reference")] = None,
    detach: Annotated[bool, typer.Option("--detach", "-d", help="Run container in background and print ID")] = False,
    memory: Annotated[Optional[str], typer.Option("--memory", "-m", help="Memory limit, e.g. 256m")] = None,
    cpu: Annotated[Optional[float], typer.Option("--cpu", help="CPU fraction [0.0-1.0]")] = None,
    pids: Annotated[Optional[int], typer.Option("--pids", help="Max PIDs")] = None,
    env_vars: Annotated[
        Optional[list[str]],
        typer.Option("--env", "-e", help="Environment variables KEY=VAL"),
    ] = None,
    volumes: Annotated[
        Optional[list[str]],
        typer.Option("--volume", "-v", help="Volume mounts SRC:DST"),
    ] = None,
    hostname: Annotated[Optional[str], typer.Option("--hostname", "-H", help="Container hostname")] = None,
    rm: Annotated[bool, typer.Option("--rm", help="Remove container on exit")] = False,
    network_mode: Annotated[str, typer.Option("--network", help="Network mode: bridge|host|none")] = "bridge",
) -> None:
    """Pull IMAGE and execute CMD inside an isolated container.

    Use --detach / -d to run in the background and get the container ID back
    immediately. The container keeps running after you exit the terminal.

    Use --name to assign a human-readable name so you can reference it with
    pybox stop <name>, pybox rm <name>, etc.
    """
    # Parse environment variables from "KEY=VAL" strings
    parsed_env: dict[str, str] = {}
    for entry in env_vars or []:
        if "=" not in entry:
            print_error(f"Invalid --env value '{entry}': expected KEY=VAL")
            raise typer.Exit(1)
        k, _, v = entry.partition("=")
        parsed_env[k] = v

    # Build cgroup spec (None if no limits requested)
    cgroup_spec: CgroupSpec | None = None
    if memory or cpu is not None or pids is not None:
        try:
            cgroup_spec = CgroupSpec(memory=memory, cpu=cpu, pids=pids)
        except Exception as exc:
            print_error(f"Invalid resource limits: {exc}")
            raise typer.Exit(1)

    manager = ContainerManager(get_config())

    try:
        container_id = asyncio.run(
            manager.create(
                image=image,
                cmd=list(cmd),
                name=name,
                env=parsed_env,
                volumes=list(volumes or []),
                hostname=hostname,
                cgroup_spec=cgroup_spec,
                network_mode=network_mode,
            )
        )
    except PyBoxError as exc:
        print_error(str(exc))
        raise typer.Exit(1)

    try:
        manager.start(container_id)
    except PyBoxError as exc:
        print_error(str(exc))
        raise typer.Exit(1)

    if detach:
        # Print ID and exit; container runs as a background orphan adopted by init.
        # Double-fork: we (A) fork child B which runs the wait loop, then A exits.
        # When A exits, B is reparented to PID 1 (systemd/init) and keeps running.
        pid_b = os.fork()
        if pid_b > 0:
            # Parent (A): print ID and exit cleanly — container stays running
            label = name or container_id[:12]
            typer.echo(container_id)
            typer.echo(f"Container '{label}' running in background.")
            raise typer.Exit(0)

        # Child B: runs in background, waits for container to finish, then cleans up.
        # We CANNOT call os.waitpid() here because detach-child is NOT the parent
        # of the container process (the CLI parent was; after CLI exits, the container
        # is adopted by PID 1/init). Instead, poll with kill(pid, 0).
        import time
        from pybox.container.state import ContainerState

        os.setsid()  # detach from controlling terminal
        null_fd = os.open(os.devnull, os.O_RDWR)
        for fd in (0, 1, 2):
            os.dup2(null_fd, fd)
        os.close(null_fd)

        state = manager._state.get(container_id)
        pid = state.get("pid")
        if pid:
            while True:
                try:
                    os.kill(pid, 0)  # 0 = check existence only, no signal sent
                    time.sleep(1)
                except (ProcessLookupError, PermissionError):
                    break  # process exited or we can no longer see it
            manager._state.update(
                container_id,
                state=ContainerState.STOPPED.value,
                pid=None,
                exit_code=0,
            )

        if rm:
            try:
                manager.remove(container_id)
            except PyBoxError:
                pass
        os._exit(0)  # noqa: SLF001

    # Foreground mode: wait for container to finish
    exit_code = manager.wait(container_id)

    if rm:
        try:
            manager.remove(container_id)
        except PyBoxError as exc:
            print_error(f"Failed to remove container: {exc}")

    raise typer.Exit(exit_code)
