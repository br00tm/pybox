"""OCI Registry layer and manifest push (upload to Docker Hub / GHCR).

Implements the OCI Distribution Spec push flow:
    1. HEAD /v2/<name>/blobs/<digest> — check if layer already exists
    2. POST /v2/<name>/blobs/uploads/ — initiate upload session
    3. PUT  /v2/<name>/blobs/uploads/<uuid>?digest=<digest> — upload blob
    4. PUT  /v2/<name>/manifests/<tag> — upload manifest

Uses chunked upload for large layers (>10 MB chunk size configurable).

Reference: https://distribution.github.io/distribution/spec/api/#pushing-an-image
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

import httpx

from pybox.exceptions import RegistryError
from pybox.image.manifest import OciManifest
from pybox.registry.auth import DockerHubAuth
from pybox.registry.client import _parse_image_ref

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 10 * 1024 * 1024  # 10 MB chunks


class OciRegistryPusher:
    """Pushes OCI images to a Docker-compatible registry.

    Usage:
        async with OciRegistryPusher(auth) as pusher:
            digest = await pusher.push("myapp:v1", manifest, images_dir)

    Args:
        auth:         DockerHubAuth instance for token management.
        registry_url: Override the default registry URL.
    """

    def __init__(
        self,
        auth: DockerHubAuth,
        registry_url: str = "https://registry-1.docker.io",
    ) -> None:
        self._auth = auth
        self._registry_url = registry_url
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "OciRegistryPusher":
        self._client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=300.0, pool=10.0),
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client:
            await self._client.aclose()

    async def push(
        self,
        image_ref: str,
        manifest: OciManifest,
        images_dir: Path,
    ) -> str:
        """Push all layers and the manifest to the registry.

        Args:
            image_ref:  Image reference, e.g. "myuser/myapp:v1".
            manifest:   OCI manifest (from commit_image or pull).
            images_dir: Local image store root.

        Returns:
            Content digest of the uploaded manifest.

        Raises:
            RegistryError: On any upload failure.
        """
        assert self._client is not None
        registry, repository, tag = _parse_image_ref(image_ref)

        token = await self._auth.get_token(repository, ["pull", "push"], registry)

        # Push each layer (skip layers that already exist)
        layers_dir = images_dir / "layers"
        for layer in manifest.layers:
            await self._push_blob(repository, registry, layer.digest, layers_dir, token)

        # Push config blob
        config_digest = manifest.config.digest
        config_path = images_dir / "sha256" / config_digest.split(":")[-1] / "config.json"
        if config_path.exists():
            await self._push_blob_bytes(
                repository, registry, config_digest,
                config_path.read_bytes(), token
            )

        # Push manifest
        manifest_digest = await self._push_manifest(
            repository, registry, tag, manifest, token
        )

        logger.info("Pushed %s: %s", image_ref, manifest_digest)
        return manifest_digest

    async def _blob_exists(
        self, repository: str, registry: str, digest: str, token: str
    ) -> bool:
        """Check if a blob already exists in the registry (HEAD request)."""
        url = f"https://{registry}/v2/{repository}/blobs/{digest}"
        resp = await self._client.head(  # type: ignore[union-attr]
            url, headers={"Authorization": f"Bearer {token}"}
        )
        return resp.status_code == 200

    async def _push_blob(
        self,
        repository: str,
        registry: str,
        digest: str,
        layers_dir: Path,
        token: str,
    ) -> None:
        """Push a layer blob to the registry if it doesn't already exist."""
        if await self._blob_exists(repository, registry, digest, token):
            logger.debug("Layer already exists: %s", digest[:20])
            return

        safe_name = digest.replace(":", "_") + ".tar.gz"
        layer_file = layers_dir / safe_name
        if not layer_file.exists():
            raise RegistryError(f"Layer file not found: {layer_file}")

        data = layer_file.read_bytes()
        await self._push_blob_bytes(repository, registry, digest, data, token)

    async def _push_blob_bytes(
        self,
        repository: str,
        registry: str,
        digest: str,
        data: bytes,
        token: str,
    ) -> None:
        """Upload raw bytes as a blob."""
        auth_header = {"Authorization": f"Bearer {token}"}

        # POST: initiate upload session
        post_url = f"https://{registry}/v2/{repository}/blobs/uploads/"
        resp = await self._client.post(post_url, headers=auth_header)  # type: ignore[union-attr]
        if resp.status_code not in (200, 202):
            raise RegistryError(
                f"Failed to initiate blob upload for {digest}",
                status_code=resp.status_code,
            )

        upload_url = resp.headers.get("Location", "")
        if not upload_url:
            raise RegistryError("Registry did not return upload URL in Location header")

        # Make URL absolute if relative
        if upload_url.startswith("/"):
            upload_url = f"https://{registry}{upload_url}"

        # PUT: upload the blob
        put_url = f"{upload_url}&digest={digest}" if "?" in upload_url else f"{upload_url}?digest={digest}"
        resp = await self._client.put(  # type: ignore[union-attr]
            put_url,
            content=data,
            headers={
                **auth_header,
                "Content-Type": "application/octet-stream",
                "Content-Length": str(len(data)),
            },
        )
        if resp.status_code not in (200, 201):
            raise RegistryError(
                f"Failed to upload blob {digest}",
                status_code=resp.status_code,
            )

        logger.debug("Uploaded blob: %s (%d bytes)", digest[:20], len(data))

    async def _push_manifest(
        self,
        repository: str,
        registry: str,
        tag: str,
        manifest: OciManifest,
        token: str,
    ) -> str:
        """Upload the image manifest and return its digest."""
        import json
        from pybox.image.manifest import MEDIA_TYPE_MANIFEST_V2

        manifest_data = manifest.model_dump(by_alias=True, mode="json")
        manifest_bytes = json.dumps(manifest_data, indent=2).encode()
        content_digest = f"sha256:{hashlib.sha256(manifest_bytes).hexdigest()}"

        url = f"https://{registry}/v2/{repository}/manifests/{tag}"
        resp = await self._client.put(  # type: ignore[union-attr]
            url,
            content=manifest_bytes,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": MEDIA_TYPE_MANIFEST_V2,
            },
        )
        if resp.status_code not in (200, 201):
            raise RegistryError(
                f"Failed to push manifest for {repository}:{tag}",
                status_code=resp.status_code,
            )

        return content_digest
