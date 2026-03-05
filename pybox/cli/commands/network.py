"""pybox network — manage virtual networks."""

from __future__ import annotations

import json
from typing import Annotated, Optional

import typer

from pybox.cli.output import print_error, print_success, print_table
from pybox.config import get_config

app = typer.Typer(help="Manage PyBox virtual networks.")


@app.command("ls")
def ls() -> None:
    """List all networks."""
    from pybox.network.manager import NetworkManager

    cfg = get_config()
    manager = NetworkManager(cfg.root / "networks")
    networks = manager.list_networks()

    if not networks:
        typer.echo("No networks found.")
        return

    headers = ["ID", "NAME", "CIDR", "GATEWAY", "DRIVER"]
    rows = [
        [n.id[:12], n.name, n.cidr, n.gateway, n.driver]
        for n in networks
    ]
    print_table(headers, rows, title="Networks")


@app.command("inspect")
def inspect(
    name: Annotated[str, typer.Argument(help="Network name")],
) -> None:
    """Show detailed JSON for a network."""
    from pybox.network.models import Network

    cfg = get_config()
    networks_dir = cfg.root / "networks"
    try:
        network = Network.load(networks_dir, name)
        typer.echo(json.dumps(network.model_dump(), indent=2))
    except FileNotFoundError:
        print_error(f"Network '{name}' not found")
        raise typer.Exit(1)


@app.command("create")
def create(
    name: Annotated[str, typer.Argument(help="Network name")],
    cidr: Annotated[str, typer.Option("--cidr", help="CIDR block, e.g. 172.16.1.0/24")] = "172.16.0.0/24",
) -> None:
    """Create a new network."""
    import uuid
    from pybox.network.bridge import BridgeManager
    from pybox.network.models import Network

    cfg = get_config()
    networks_dir = cfg.root / "networks"

    import ipaddress
    try:
        net = ipaddress.IPv4Network(cidr, strict=False)
        gateway = str(list(net.hosts())[0])
    except ValueError as exc:
        print_error(f"Invalid CIDR: {exc}")
        raise typer.Exit(1)

    bridge = BridgeManager(name)
    try:
        bridge.create_bridge(f"{gateway}/{net.prefixlen}")
        bridge.enable_ip_forward()
    except Exception as exc:
        print_error(f"Failed to create bridge: {exc}")
        raise typer.Exit(1)

    network = Network(
        id=uuid.uuid4().hex[:12],
        name=name,
        cidr=cidr,
        gateway=gateway,
        bridge=name,
    )
    network.save(networks_dir)
    print_success(f"Network '{name}' created ({cidr})")


@app.command("rm")
def rm(
    name: Annotated[str, typer.Argument(help="Network name to remove")],
) -> None:
    """Remove a network."""
    from pybox.network.bridge import BridgeManager
    from pybox.network.models import Network

    cfg = get_config()
    networks_dir = cfg.root / "networks"

    try:
        network = Network.load(networks_dir, name)
    except FileNotFoundError:
        print_error(f"Network '{name}' not found")
        raise typer.Exit(1)

    bridge = BridgeManager(network.bridge)
    bridge.delete_bridge()

    (networks_dir / f"{name}.json").unlink(missing_ok=True)
    print_success(f"Network '{name}' removed")
