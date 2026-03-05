"""pybox push — push an image to a registry."""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer

from pybox.cli.output import print_error, print_success

app = typer.Typer(help="Push an image to a registry.")


@app.callback(invoke_without_command=True)
def push(
    image_ref: Annotated[str, typer.Argument(help="Image reference, e.g. myuser/myapp:v1")],
) -> None:
    """Push IMAGE_REF to its registry."""
    from pybox.config import get_config
    from pybox.image.tag import TagManager
    from pybox.image.manifest import parse_manifest
    from pybox.registry.auth import DockerHubAuth
    from pybox.registry.client import _parse_image_ref
    from pybox.registry.push import OciRegistryPusher
    from pybox.exceptions import ImageNotFoundError, PyBoxError
    import json

    cfg = get_config()
    registry, repository, tag = _parse_image_ref(image_ref)

    # Resolve tag to digest
    try:
        tag_mgr = TagManager(cfg.images_dir)
        digest = tag_mgr.resolve(image_ref)
    except ImageNotFoundError:
        print_error(f"Image '{image_ref}' not found locally. Build or pull it first.")
        raise typer.Exit(1)

    # Load manifest
    manifest_path = cfg.images_dir / "sha256" / digest.split(":")[-1] / "manifest.json"
    if not manifest_path.exists():
        print_error(f"Manifest not found for {image_ref}")
        raise typer.Exit(1)

    manifest = parse_manifest(json.loads(manifest_path.read_text()))

    # Load credentials
    creds = DockerHubAuth.load_credentials(registry)
    if creds:
        username, password = creds
        auth = DockerHubAuth(username=username, password=password)
    else:
        auth = DockerHubAuth()

    async def _push() -> str:
        async with OciRegistryPusher(auth) as pusher:
            return await pusher.push(image_ref, manifest, cfg.images_dir)

    try:
        result_digest = asyncio.run(_push())
        short = result_digest.split(":")[-1][:12]
        print_success(f"Pushed {image_ref} → sha256:{short}")
    except PyBoxError as exc:
        print_error(str(exc))
        raise typer.Exit(1)
