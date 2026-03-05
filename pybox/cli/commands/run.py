"""pybox run — pull an image and start a container interactively.

Usage:
    pybox run --image ubuntu:24.04 -- /bin/bash
    pybox run -i ubuntu:24.04 -m 256m --cpu 0.5 -e FOO=bar -- /bin/sh -c "echo hi"
    pybox run --image alpine:latest --rm -- /bin/echo hello
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Optional

import typer

from pybox.cgroups.specs import CgroupSpec
from pybox.cli.output import print_error
from pybox.config import get_config
from pybox.container.runtime import ContainerManager
from pybox.exceptions import PyBoxError

app = typer.Typer(help="Run a container from an image.")


@app.callback(invoke_without_command=True)
def run(
    image: Annotated[str, typer.Option("--image", "-i", help="Image reference, e.g. ubuntu:24.04")],
    cmd: Annotated[list[str], typer.Argument(help="Command to run inside the container")],
    name: Annotated[Optional[str], typer.Option("--name", help="Container name")] = None,
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
    hostname: Annotated[Optional[str], typer.Option("--hostname", "-h", help="Container hostname")] = None,
    rm: Annotated[bool, typer.Option("--rm", help="Remove container on exit")] = False,
    network_mode: Annotated[str, typer.Option("--network", help="Network mode: bridge|host|none")] = "bridge",
) -> None:
    """Pull IMAGE and execute CMD inside an isolated container."""
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

    typer.echo(f"Container ID: {container_id}")

    try:
        pid = manager.start(container_id)
    except PyBoxError as exc:
        print_error(str(exc))
        raise typer.Exit(1)

    # Attach stdin/stdout/stderr to the container via dup2 if running interactively
    _attach_tty(pid)

    # Wait for the container to finish
    exit_code = manager.wait(container_id)

    if rm:
        try:
            manager.remove(container_id)
        except PyBoxError as exc:
            print_error(f"Failed to remove container: {exc}")

    raise typer.Exit(exit_code)


def _attach_tty(pid: int) -> None:
    """Redirect parent stdin/stdout/stderr to the container process.

    This is a best-effort operation for interactive sessions. For a full TTY
    experience, the container should be started with os.openpty() — that is
    handled in a future phase (daemon + exec).

    Args:
        pid: PID of the container init process.
    """
    # In Phase 1 we do not set up a pseudo-terminal; the child process
    # inherits the parent's file descriptors through fork(). No dup2 needed
    # for simple non-interactive commands. Interactive shell support with a
    # proper PTY will be added in Phase 4 (pybox exec).
    pass
