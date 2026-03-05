"""PyBox root CLI application.

Entry point registered in pyproject.toml as:
    pybox = "pybox.cli.main:app"

Sub-commands:
    run       — pull an image and start a container
    build     — build an image from boxfile.toml
    ps        — list containers
    logs      — stream container logs
    exec      — execute a command in a running container
    stop      — stop one or more containers
    rm        — remove one or more containers
    images    — list local images
    rmi       — remove local images
    pull      — pull an image from a registry
    push      — push an image to a registry
    login     — authenticate with a registry
    network   — manage virtual networks
    info      — show system information
"""

from __future__ import annotations

from typing import Annotated

import typer

from pybox import __version__
from pybox.cli.output import print_banner
from pybox.cli.commands import run as run_mod
from pybox.cli.commands import build as build_mod
from pybox.cli.commands import ps as ps_mod
from pybox.cli.commands import logs as logs_mod
from pybox.cli.commands import exec as exec_mod
from pybox.cli.commands import stop as stop_mod
from pybox.cli.commands import rm as rm_mod
from pybox.cli.commands import images as images_mod
from pybox.cli.commands import rmi as rmi_mod
from pybox.cli.commands import pull as pull_mod
from pybox.cli.commands import push as push_mod
from pybox.cli.commands import login as login_mod
from pybox.cli.commands import network as network_mod
from pybox.cli.commands import info as info_mod

app = typer.Typer(
    name="pybox",
    help="A Python-native container runtime — containers, reimagined.",
    add_completion=True,
    rich_markup_mode="rich",
)


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", "-V", help="Show PyBox version and exit.", is_eager=True),
    ] = False,
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Enable verbose debug logging."),
    ] = False,
) -> None:
    """PyBox — A Python-native container runtime."""
    if version:
        print_banner()
        typer.echo(f"PyBox version {__version__}")
        raise typer.Exit()

    if debug:
        import logging
        logging.basicConfig(level=logging.DEBUG)


# ------------------------------------------------------------------
# Register all sub-commands
# ------------------------------------------------------------------

app.add_typer(run_mod.app, name="run")
app.add_typer(build_mod.app, name="build")
app.add_typer(ps_mod.app, name="ps")
app.add_typer(logs_mod.app, name="logs")
app.add_typer(exec_mod.app, name="exec")
app.add_typer(stop_mod.app, name="stop")
app.add_typer(rm_mod.app, name="rm")
app.add_typer(images_mod.app, name="images")
app.add_typer(rmi_mod.app, name="rmi")
app.add_typer(pull_mod.app, name="pull")
app.add_typer(push_mod.app, name="push")
app.add_typer(login_mod.app, name="login")
app.add_typer(network_mod.app, name="network")
app.add_typer(info_mod.app, name="info")
