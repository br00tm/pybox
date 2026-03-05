"""pybox images — list locally stored images."""

from __future__ import annotations

import json
from typing import Annotated, Optional

import typer

from pybox.cli.output import format_size, print_table

app = typer.Typer(help="List local images.")


@app.callback(invoke_without_command=True)
def images(
    all_images: Annotated[bool, typer.Option("--all", "-a", help="Show intermediate layers too")] = False,
    fmt: Annotated[str, typer.Option("--format", help="Output format: table|json")] = "table",
    filter_kv: Annotated[Optional[str], typer.Option("--filter", help="Filter key=val")] = None,
) -> None:
    """List locally available images."""
    from pybox.config import get_config

    cfg = get_config()
    sha256_dir = cfg.images_dir / "sha256"
    tags_file = cfg.images_dir / "tags.json"

    # Load tag index: digest → list of tags
    digest_to_tags: dict[str, list[str]] = {}
    if tags_file.exists():
        tag_map: dict[str, str] = json.loads(tags_file.read_text())
        for tag, digest in tag_map.items():
            short_digest = digest.split(":")[-1]
            digest_to_tags.setdefault(short_digest, []).append(tag)

    if not sha256_dir.exists():
        typer.echo("No images found.")
        return

    rows: list[dict[str, str]] = []
    for entry in sorted(sha256_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not entry.is_dir():
            continue
        manifest_file = entry / "manifest.json"
        if not manifest_file.exists():
            continue

        short_id = entry.name[:12]
        size = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
        tags = digest_to_tags.get(entry.name, ["<none>"])

        for tag in tags:
            # Parse tag into repo:tag
            if ":" in tag:
                repo, t = tag.rsplit(":", 1)
            else:
                repo, t = tag, "latest"

            import datetime
            mtime = datetime.datetime.fromtimestamp(
                manifest_file.stat().st_mtime
            ).strftime("%Y-%m-%d %H:%M")

            rows.append({
                "repo": repo,
                "tag": t,
                "id": short_id,
                "created": mtime,
                "size": format_size(size),
            })

    # Apply filter
    if filter_kv and "=" in filter_kv:
        fk, fv = filter_kv.split("=", 1)
        rows = [r for r in rows if r.get(fk, "").lower() == fv.lower()]

    if not rows:
        typer.echo("No images found.")
        return

    if fmt == "json":
        typer.echo(json.dumps(rows, indent=2))
        return

    headers = ["REPOSITORY", "TAG", "IMAGE ID", "CREATED", "SIZE"]
    table_rows = [[r["repo"], r["tag"], r["id"], r["created"], r["size"]] for r in rows]
    print_table(headers, table_rows, title="Images")
