"""pybox ps — list containers."""

from __future__ import annotations

from typing import Annotated

import typer

from pybox.cli.output import print_table

app = typer.Typer(help="List containers.")


@app.callback(invoke_without_command=True)
def ps(
    all_containers: Annotated[bool, typer.Option("--all", "-a", help="Show all containers")] = False,
    fmt: Annotated[str, typer.Option("--format", help="Output format: table|json")] = "table",
) -> None:
    """List running containers (or all with --all)."""
    import json as _json
    from pybox.config import get_config
    from pybox.container.runtime import ContainerManager

    manager = ContainerManager(get_config())
    containers = manager.list_containers(all_states=all_containers)

    if fmt == "json":
        typer.echo(_json.dumps(containers, indent=2))
        return

    if not containers:
        typer.echo("No containers found.")
        return

    headers = ["ID", "NAME", "IMAGE", "STATE", "PID", "UPTIME"]
    rows = [
        [
            c.get("id", "")[:12],
            c.get("name") or "",
            c.get("image", ""),
            c.get("state", ""),
            str(c.get("pid") or ""),
            "",
        ]
        for c in containers
    ]
    print_table(headers, rows, title="Containers")
