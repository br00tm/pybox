"""pybox login — authenticate with an OCI registry."""

from __future__ import annotations

import asyncio
from typing import Annotated, Optional

import typer

from pybox.cli.output import print_error, print_success

app = typer.Typer(help="Authenticate with an OCI registry.")


@app.callback(invoke_without_command=True)
def login(
    registry: Annotated[str, typer.Argument(help="Registry hostname")] = "registry-1.docker.io",
    username: Annotated[Optional[str], typer.Option("--username", "-u", help="Username")] = None,
    password: Annotated[Optional[str], typer.Option("--password", "-p", help="Password or token", hide_input=True)] = None,
) -> None:
    """Log in to REGISTRY (default: Docker Hub)."""
    from pybox.registry.auth import DockerHubAuth
    from pybox.exceptions import RegistryError

    if username is None:
        username = typer.prompt("Username")
    if password is None:
        password = typer.prompt("Password", hide_input=True)

    auth = DockerHubAuth(username=username, password=password)

    # Validate credentials by fetching a token
    try:
        asyncio.run(auth.get_token("library/hello-world", ["pull"], registry))
    except RegistryError as exc:
        print_error(f"Login failed: {exc}")
        raise typer.Exit(1)

    # Save credentials
    DockerHubAuth.save_credentials(registry, username, password)
    print_success(f"Logged in to {registry} as {username}")
