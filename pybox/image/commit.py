"""Commit a list of layer tarballs into a valid OCI image manifest + config.

Takes the accumulated layer tars from a build, computes their digests,
and writes a complete OCI image to the local image store.

Output structure under <PYBOX_ROOT>/images/sha256/<config-digest>/:
    manifest.json   — OCI image manifest (schemaVersion 2)
    config.json     — OCI image config (Cmd, Env, rootfs diff IDs)

Reference:
    https://github.com/opencontainers/image-spec/blob/main/manifest.md
    https://github.com/opencontainers/image-spec/blob/main/config.md
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import shutil
from pathlib import Path
from typing import Any

from pybox.image.build_spec import BuildSpec
from pybox.image.manifest import (
    MEDIA_TYPE_CONFIG,
    MEDIA_TYPE_LAYER,
    MEDIA_TYPE_MANIFEST_V2,
)

logger = logging.getLogger(__name__)


def _sha256_file(path: Path) -> str:
    """Compute the sha256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_uncompressed(gz_path: Path) -> str:
    """Compute sha256 of the uncompressed content of a .gz file (diffId)."""
    h = hashlib.sha256()
    with gzip.open(gz_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def commit_image(
    layer_tars: list[Path],
    base_config: dict[str, Any],
    build_spec: BuildSpec,
    tag: str,
    images_dir: Path,
) -> str:
    """Assemble layer tars into an OCI image and write it to the image store.

    Args:
        layer_tars:  Ordered list of layer .tar.gz paths (base first).
        base_config: OCI config dict from the base image (may be {}).
        build_spec:  Parsed BuildSpec providing Cmd, Env, ExposedPorts.
        tag:         Human-readable tag for this image, e.g. "myapp:v1".
        images_dir:  Root of the local image store.

    Returns:
        The sha256 digest of the image config blob (the "image ID").
    """
    # ------------------------------------------------------------------
    # 1. Copy layers into the image store and compute their digests
    # ------------------------------------------------------------------
    layers_store_dir = images_dir / "layers"
    layers_store_dir.mkdir(parents=True, exist_ok=True)

    layer_descriptors: list[dict[str, Any]] = []
    diff_ids: list[str] = []

    for tar_path in layer_tars:
        compressed_hex = _sha256_file(tar_path)
        uncompressed_hex = _sha256_uncompressed(tar_path)
        size = tar_path.stat().st_size

        # Store layer under its content digest
        dest = layers_store_dir / f"sha256_{compressed_hex}.tar.gz"
        if not dest.exists():
            shutil.copy2(tar_path, dest)

        layer_descriptors.append({
            "mediaType": MEDIA_TYPE_LAYER,
            "digest": f"sha256:{compressed_hex}",
            "size": size,
        })
        diff_ids.append(f"sha256:{uncompressed_hex}")

    # ------------------------------------------------------------------
    # 2. Build the OCI config JSON
    # ------------------------------------------------------------------
    run_spec = build_spec.run
    env_list: list[str] = [f"{k}={v}" for k, v in run_spec.env.items()]

    # Merge base image env (base first, then build spec overrides)
    base_env: list[str] = base_config.get("config", {}).get("Env") or []
    merged_env = _merge_env(base_env, env_list)

    exposed_ports: dict[str, Any] = {
        f"{port}/tcp": {} for port in run_spec.expose
    }

    config_json: dict[str, Any] = {
        "architecture": "amd64",
        "os": "linux",
        "config": {
            "Cmd": run_spec.cmd or base_config.get("config", {}).get("Cmd"),
            "Entrypoint": base_config.get("config", {}).get("Entrypoint"),
            "Env": merged_env,
            "WorkingDir": base_config.get("config", {}).get("WorkingDir", ""),
            "User": run_spec.user or base_config.get("config", {}).get("User", ""),
            "ExposedPorts": exposed_ports,
            "Labels": build_spec.box.meta.labels,
        },
        "rootfs": {
            "type": "layers",
            "diff_ids": diff_ids,
        },
        "history": [],
    }

    config_bytes = json.dumps(config_json, indent=2, sort_keys=True).encode()
    config_hex = hashlib.sha256(config_bytes).hexdigest()
    config_digest = f"sha256:{config_hex}"

    # ------------------------------------------------------------------
    # 3. Build the OCI manifest JSON
    # ------------------------------------------------------------------
    config_size = len(config_bytes)
    manifest_json: dict[str, Any] = {
        "schemaVersion": 2,
        "mediaType": MEDIA_TYPE_MANIFEST_V2,
        "config": {
            "mediaType": MEDIA_TYPE_CONFIG,
            "digest": config_digest,
            "size": config_size,
        },
        "layers": layer_descriptors,
    }

    # ------------------------------------------------------------------
    # 4. Write to the image store
    # ------------------------------------------------------------------
    image_dir = images_dir / "sha256" / config_hex
    image_dir.mkdir(parents=True, exist_ok=True)

    (image_dir / "config.json").write_bytes(config_bytes)
    (image_dir / "manifest.json").write_text(
        json.dumps(manifest_json, indent=2)
    )

    # ------------------------------------------------------------------
    # 5. Write / update the tag index
    # ------------------------------------------------------------------
    _update_tags(images_dir, tag, config_digest)

    logger.info(
        "Committed image %s: %d layers, config digest %s",
        tag,
        len(layer_tars),
        config_digest,
    )
    return config_digest


def _merge_env(base: list[str], override: list[str]) -> list[str]:
    """Merge two KEY=VAL env lists; override wins on duplicate keys."""
    env: dict[str, str] = {}
    for entry in base + override:
        k, _, v = entry.partition("=")
        env[k] = v
    return [f"{k}={v}" for k, v in env.items()]


def _update_tags(images_dir: Path, tag: str, config_digest: str) -> None:
    """Update the tags.json index to map tag → config_digest."""
    tags_file = images_dir / "tags.json"
    if tags_file.exists():
        tags: dict[str, str] = json.loads(tags_file.read_text())
    else:
        tags = {}
    tags[tag] = config_digest
    tags_file.write_text(json.dumps(tags, indent=2))
