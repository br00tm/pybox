"""pybox rmi — remove local images."""

from __future__ import annotations

import shutil
from typing import Annotated

import typer

from pybox.cli.output import format_size, print_error, print_success

app = typer.Typer(help="Remove local images.")


@app.callback(invoke_without_command=True)
def rmi(
    image_refs: Annotated[list[str], typer.Argument(help="Image reference(s) to remove")],
    force: Annotated[bool, typer.Option("--force", "-f", help="Force removal even if in use")] = False,
) -> None:
    """Remove IMAGE_REFS from local storage."""
    import json
    from pybox.config import get_config
    from pybox.container.state import StateManager

    cfg = get_config()
    sha256_dir = cfg.images_dir / "sha256"
    tags_file = cfg.images_dir / "tags.json"

    # Load tag map
    tags: dict[str, str] = {}
    if tags_file.exists():
        tags = json.loads(tags_file.read_text())

    # Check if any running containers reference this image
    state_mgr = StateManager(cfg.containers_dir)
    running_images: set[str] = {
        c["image"] for c in state_mgr.list_all() if c.get("state") == "running"
    }

    had_error = False
    for ref in image_refs:
        # Resolve to digest
        digest_hex: str | None = None
        if ref.startswith("sha256:"):
            digest_hex = ref.split(":")[-1]
        elif ref in tags:
            digest_hex = tags[ref].split(":")[-1]
        else:
            # Try partial match
            for tag_key, tag_digest in tags.items():
                if ref in tag_key:
                    digest_hex = tag_digest.split(":")[-1]
                    break

        if digest_hex is None:
            print_error(f"Image '{ref}' not found")
            had_error = True
            continue

        if not force and ref in running_images:
            print_error(
                f"Image '{ref}' is used by a running container. Use --force to remove."
            )
            had_error = True
            continue

        image_dir = sha256_dir / digest_hex
        if not image_dir.exists():
            print_error(f"Image directory not found for '{ref}'")
            had_error = True
            continue

        # Calculate freed size before deletion
        size = sum(f.stat().st_size for f in image_dir.rglob("*") if f.is_file())

        shutil.rmtree(image_dir)

        # Remove from tags index
        to_remove = [k for k, v in tags.items() if v.split(":")[-1] == digest_hex]
        for k in to_remove:
            del tags[k]
        tags_file.write_text(json.dumps(tags, indent=2))

        print_success(f"Removed {ref} (freed {format_size(size)})")

    if had_error:
        raise typer.Exit(1)
