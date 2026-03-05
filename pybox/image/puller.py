"""Image puller: download OCI images from a registry and cache them locally.

Orchestrates the full pull flow:
    1. Parse the image reference (registry/repo:tag)
    2. Check local cache — if image layers already exist, skip download
    3. Fetch the manifest from the registry
    4. Download all layers concurrently (up to 4 at a time)
    5. Verify sha256 digests
    6. Extract layer tarballs to per-digest directories
    7. Save manifest.json and config.json to the image store

Storage layout (under PYBOX_ROOT/images/):
    images/
    ├── sha256/<config-digest>/
    │   ├── manifest.json
    │   └── config.json
    └── layers/
        └── sha256_<layer-digest>.tar.gz
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from pybox.exceptions import ImagePullError
from pybox.image.layer import apply_layers
from pybox.image.manifest import OciManifest, parse_manifest
from pybox.registry.client import RegistryClient, _parse_image_ref

logger = logging.getLogger(__name__)


class ImagePuller:
    """Downloads and caches OCI images from a registry.

    Usage:
        puller = ImagePuller(images_dir=config.images_dir)
        layer_dirs = await puller.pull("ubuntu:24.04")
    """

    def __init__(
        self,
        images_dir: Path,
        registry_url: str = "https://registry-1.docker.io",
        auth_url: str = "https://auth.docker.io",
        username: str | None = None,
        password: str | None = None,
        mirror_url: str | None = None,
    ) -> None:
        self._images_dir = images_dir
        self._registry_url = registry_url
        self._auth_url = auth_url
        self._username = username
        self._password = password
        self._mirror_url = mirror_url

    async def pull(self, image_ref: str) -> tuple[OciManifest, list[Path]]:
        """Pull an image and return its manifest and extracted layer directories.

        If the image is already cached locally (all layer dirs exist), the
        download is skipped and the cached data is returned.

        Args:
            image_ref: Image reference, e.g. "ubuntu:24.04" or "ghcr.io/org/app:v1".

        Returns:
            Tuple of (OciManifest, list of extracted layer directories base-first).

        Raises:
            ImagePullError: If the pull or extraction fails.
        """
        registry, repository, tag = _parse_image_ref(image_ref)
        logger.info("Pulling image %s from %s", image_ref, registry)

        try:
            async with RegistryClient(
                registry_url=self._registry_url,
                auth_url=self._auth_url,
                username=self._username,
                password=self._password,
                mirror_url=self._mirror_url,
            ) as client:
                manifest_dict = await client.get_manifest(image_ref)
                manifest = parse_manifest(manifest_dict)

                config_digest = manifest.config.digest
                manifest_dir = self._images_dir / "sha256" / _safe_digest(config_digest)

                # Check if already fully pulled
                if self._is_cached(manifest, manifest_dir):
                    logger.info("Image %s already cached", image_ref)
                    layer_dirs = self._layer_dirs_for(manifest)
                    return manifest, layer_dirs

                # Download layers concurrently (cap at 4 to avoid rate limits)
                layers_download_dir = self._images_dir / "layers"
                semaphore = asyncio.Semaphore(4)

                async def _pull_one(layer_digest: str) -> Path:
                    async with semaphore:
                        return await client.pull_layer(
                            repository, registry, layer_digest, layers_download_dir
                        )

                layer_digests = manifest.layer_digests()
                logger.debug("Downloading %d layers for %s", len(layer_digests), image_ref)
                layer_tarballs: list[Path] = list(
                    await asyncio.gather(*[_pull_one(d) for d in layer_digests])
                )

                # Extract layers into per-digest directories
                extracted_base = self._images_dir / "extracted"
                layer_dirs = apply_layers(layer_tarballs, extracted_base)

                # Persist manifest
                manifest_dir.mkdir(parents=True, exist_ok=True)
                (manifest_dir / "manifest.json").write_text(
                    json.dumps(manifest_dict, indent=2)
                )

                # Download and save config blob
                config_raw = await self._download_config(client, repository, registry, config_digest)
                (manifest_dir / "config.json").write_text(json.dumps(config_raw, indent=2))

                logger.info(
                    "Pulled %s: %d layers, config digest %s",
                    image_ref,
                    len(layer_dirs),
                    config_digest,
                )
                return manifest, layer_dirs

        except ImagePullError:
            raise
        except Exception as exc:
            raise ImagePullError(
                f"Failed to pull image '{image_ref}'", details=str(exc)
            ) from exc

    async def _download_config(
        self,
        client: RegistryClient,
        repository: str,
        registry: str,
        digest: str,
    ) -> dict[str, Any]:
        """Download the image config blob and return it as a dict."""
        config_path = self._images_dir / "layers" / f"{_safe_digest(digest)}.json"
        if config_path.exists():
            return json.loads(config_path.read_text())

        layer_path = await client.pull_layer(
            repository, registry, digest, self._images_dir / "layers"
        )
        # The config blob is JSON, not a tarball — read it back as JSON
        try:
            data: dict[str, Any] = json.loads(layer_path.read_bytes())
        except (json.JSONDecodeError, UnicodeDecodeError):
            # Fallback: return empty config if blob is not valid JSON
            logger.warning("Config blob for %s is not valid JSON", digest)
            data = {}
        return data

    def _is_cached(self, manifest: OciManifest, manifest_dir: Path) -> bool:
        """Return True if all layers are already extracted locally."""
        if not (manifest_dir / "manifest.json").exists():
            return False
        extracted_base = self._images_dir / "extracted"
        for layer_path in self._layer_tarballs_for(manifest):
            digest_name = layer_path.stem.replace(".tar", "")
            if not (extracted_base / digest_name / ".extracted").exists():
                return False
        return True

    def _layer_tarballs_for(self, manifest: OciManifest) -> list[Path]:
        """Return the expected tarball paths for a manifest's layers."""
        layers_dir = self._images_dir / "layers"
        return [
            layers_dir / f"{d.replace(':', '_')}.tar.gz"
            for d in manifest.layer_digests()
        ]

    def _layer_dirs_for(self, manifest: OciManifest) -> list[Path]:
        """Return the expected extracted-layer dirs for a cached manifest."""
        extracted_base = self._images_dir / "extracted"
        result: list[Path] = []
        for tarball in self._layer_tarballs_for(manifest):
            digest_name = tarball.stem.replace(".tar", "")
            result.append(extracted_base / digest_name)
        return result


def _safe_digest(digest: str) -> str:
    """Convert a digest like 'sha256:abc...' to a filesystem-safe string."""
    return digest.replace(":", "_").replace("/", "_")
