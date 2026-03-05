"""pybox build — build an OCI image from a boxfile.toml.

Usage:
    pybox build -f boxfile.toml -t myapp:v1
    pybox build --no-cache -t myapp:v1
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.panel import Panel

from pybox.cli.output import print_error, print_success, format_size
from pybox.config import get_config
from pybox.exceptions import PyBoxError

app = typer.Typer(help="Build an image from a boxfile.toml.")
console = Console()


@app.callback(invoke_without_command=True)
def build(
    file: Annotated[Path, typer.Option("--file", "-f", help="Path to boxfile.toml")] = Path("boxfile.toml"),
    tag: Annotated[str, typer.Option("--tag", "-t", help="Image tag, e.g. myapp:v1")],
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Disable layer cache")] = False,
) -> None:
    """Build an OCI image from BOXFILE (default: boxfile.toml) and tag it."""
    from pybox.image.builder import ImageBuilder
    from pybox.image.parser import parse_boxfile

    # Parse the build spec
    try:
        spec = parse_boxfile(file)
    except FileNotFoundError:
        print_error(f"boxfile not found: {file}")
        raise typer.Exit(1)
    except Exception as exc:
        print_error(f"Failed to parse {file}: {exc}")
        raise typer.Exit(1)

    # Show build summary
    step_count = len(spec.steps) + (1 if spec.python else 0)
    console.print(
        Panel(
            f"[bold]Image:[/bold]    {tag}\n"
            f"[bold]Base:[/bold]     {spec.box.base}\n"
            f"[bold]Steps:[/bold]    {step_count}\n"
            f"[bold]Cache:[/bold]    {'disabled' if no_cache else 'enabled'}",
            title="[bold cyan]PyBox Build[/bold cyan]",
            border_style="cyan",
        )
    )

    cfg = get_config()
    builder = ImageBuilder(config=cfg, no_cache=no_cache)

    try:
        digest = asyncio.run(builder.build(spec, tag))
    except PyBoxError as exc:
        print_error(str(exc))
        raise typer.Exit(1)
    except Exception as exc:
        print_error(f"Build failed: {exc}")
        raise typer.Exit(1)

    # Show output image info
    short_digest = digest.split(":")[-1][:12]
    image_dir = cfg.images_dir / "sha256" / digest.split(":")[-1]
    if image_dir.exists():
        size = sum(f.stat().st_size for f in image_dir.rglob("*") if f.is_file())
        size_str = format_size(size)
    else:
        size_str = "unknown"

    print_success(
        f"Built {tag} → sha256:{short_digest} ({size_str})"
    )
