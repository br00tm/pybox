"""pybox info — show system and runtime information."""

from __future__ import annotations

import platform
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from pybox.cli.output import format_size

app = typer.Typer(help="Display system information.")
console = Console()


@app.callback(invoke_without_command=True)
def info() -> None:
    """Show PyBox runtime and system information."""
    from pybox import __version__
    from pybox.config import get_config
    from pybox.container.state import StateManager
    from pybox.rootless import is_rootless

    cfg = get_config()

    # Detect storage driver
    storage_driver = "overlay"
    if is_rootless():
        import shutil
        storage_driver = "fuse-overlayfs" if shutil.which("fuse-overlayfs") else "vfs"

    # Count images and containers
    sha256_dir = cfg.images_dir / "sha256"
    image_count = len(list(sha256_dir.iterdir())) if sha256_dir.exists() else 0

    state_mgr = StateManager(cfg.containers_dir)
    all_containers = state_mgr.list_all()
    running = sum(1 for c in all_containers if c.get("state") == "running")
    stopped = len(all_containers) - running

    # Calculate disk usage
    def _dir_size(path: Path) -> int:
        if not path.exists():
            return 0
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())

    total_size = _dir_size(cfg.root)

    # Detect cgroups version
    cgroup_v2 = Path("/sys/fs/cgroup/cgroup.controllers").exists()
    cgroup_ver = "v2" if cgroup_v2 else "v1"

    # Build info table
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("Key", style="bold cyan", no_wrap=True)
    table.add_column("Value")

    rows = [
        ("PyBox Version", __version__),
        ("Rootless Mode", str(is_rootless())),
        ("Storage Driver", storage_driver),
        ("Cgroups Version", cgroup_ver),
        ("Kernel Version", platform.release()),
        ("Architecture", platform.machine()),
        ("Python Version", platform.python_version()),
        ("PyBox Root", str(cfg.root)),
        ("Cgroup Root", str(cfg.cgroup_root)),
        ("Images", str(image_count)),
        ("Containers (running)", str(running)),
        ("Containers (stopped)", str(stopped)),
        ("Disk Usage", format_size(total_size)),
    ]

    # Check optional dependencies
    import shutil
    optional = [
        ("fuse-overlayfs", shutil.which("fuse-overlayfs") is not None),
        ("slirp4netns", shutil.which("slirp4netns") is not None),
        ("newuidmap", shutil.which("newuidmap") is not None),
        ("nft (nftables)", shutil.which("nft") is not None),
    ]
    for name, available in optional:
        status = "[green]available[/green]" if available else "[dim]not found[/dim]"
        rows.append((f"  {name}", status))

    for key, val in rows:
        table.add_row(key, val)

    console.print("\n[bold cyan]PyBox System Information[/bold cyan]\n")
    console.print(table)
    console.print()
