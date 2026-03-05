"""pybox pull — pull an image from a registry."""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer

from pybox.cli.output import print_error, print_success

app = typer.Typer(help="Pull an image from a registry.")


@app.callback(invoke_without_command=True)
def pull(
    image_ref: Annotated[str, typer.Argument(help="Image reference, e.g. ubuntu:24.04")],
) -> None:
    """Pull IMAGE_REF from its registry and store it locally."""
    from pybox.config import get_config
    from pybox.image.puller import ImagePuller
    from pybox.exceptions import PyBoxError

    cfg = get_config()
    puller = ImagePuller(images_dir=cfg.images_dir)

    try:
        manifest, layer_dirs = asyncio.run(puller.pull(image_ref))
        print_success(
            f"Pulled {image_ref}: {len(layer_dirs)} layer(s)"
        )
    except PyBoxError as exc:
        print_error(str(exc))
        raise typer.Exit(1)
