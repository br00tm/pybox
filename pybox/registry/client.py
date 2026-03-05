"""Async OCI registry client for Docker Hub image pulls.

Implements the Docker Registry HTTP API V2 (also OCI Distribution Spec):
    https://distribution.github.io/distribution/spec/api/

Auth flow for Docker Hub:
    1. GET /v2/  → 401 Www-Authenticate: Bearer realm=...,service=...,scope=...
    2. GET <realm>?service=<service>&scope=<scope> → {"token": "..."}
    3. Use 'Authorization: Bearer <token>' on subsequent requests

Layers are downloaded as gzip-compressed tarballs and verified by sha256
digest before being passed to image.layer for extraction.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Any

import httpx

from pybox.exceptions import ImageNotFoundError, RegistryError

logger = logging.getLogger(__name__)

# OCI / Docker manifest media types
MANIFEST_V2_SCHEMA2 = "application/vnd.docker.distribution.manifest.v2+json"
MANIFEST_OCI = "application/vnd.oci.image.manifest.v1+json"
MANIFEST_LIST = "application/vnd.docker.distribution.manifest.list.v2+json"
MANIFEST_OCI_INDEX = "application/vnd.oci.image.index.v1+json"

ACCEPT_MANIFESTS = ", ".join(
    [MANIFEST_V2_SCHEMA2, MANIFEST_OCI, MANIFEST_LIST, MANIFEST_OCI_INDEX]
)


def _parse_image_ref(image: str) -> tuple[str, str, str]:
    """Parse an image reference into (registry, repository, tag).

    Handles:
        - "ubuntu:24.04"          → ("registry-1.docker.io", "library/ubuntu", "24.04")
        - "myuser/myapp:latest"   → ("registry-1.docker.io", "myuser/myapp", "latest")
        - "ghcr.io/org/app:v1"   → ("ghcr.io", "org/app", "v1")

    Args:
        image: Docker image reference string.

    Returns:
        Tuple of (registry_host, repository, tag).
    """
    # Separate tag
    if ":" in image.split("/")[-1]:
        name, tag = image.rsplit(":", 1)
    else:
        name, tag = image, "latest"

    # Detect explicit registry (contains a dot or colon before first slash)
    parts = name.split("/")
    if len(parts) >= 2 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        registry = parts[0]
        repository = "/".join(parts[1:])
    else:
        # Default to Docker Hub
        registry = "registry-1.docker.io"
        # Official images live under the "library/" namespace
        repository = name if "/" in name else f"library/{name}"

    return registry, repository, tag


class RegistryClient:
    """Async client for pulling OCI images from a Docker-compatible registry.

    Usage:
        async with RegistryClient() as client:
            manifest = await client.get_manifest("ubuntu", "24.04")
            await client.pull_image("ubuntu:24.04", dest_dir)
    """

    def __init__(
        self,
        registry_url: str = "https://registry-1.docker.io",
        auth_url: str = "https://auth.docker.io",
        username: str | None = None,
        password: str | None = None,
        mirror_url: str | None = None,
    ) -> None:
        self._registry_url = mirror_url or registry_url
        self._auth_url = auth_url
        self._username = username
        self._password = password
        self._tokens: dict[str, str] = {}  # scope → bearer token cache
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "RegistryClient":
        # Follow redirects, set a generous timeout for layer downloads
        self._client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=60.0, pool=10.0),
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    async def _get_token(self, repository: str, registry: str) -> str:
        """Obtain a Bearer token for the given repository scope.

        Docker Hub auth flow:
            GET <auth_url>/token?service=registry.docker.io&scope=repository:<repo>:pull
            → JSON {"token": "<jwt>"}

        Args:
            repository: e.g. "library/ubuntu"
            registry:   registry host, e.g. "registry-1.docker.io"

        Returns:
            Bearer token string.
        """
        scope = f"repository:{repository}:pull"
        if scope in self._tokens:
            return self._tokens[scope]

        assert self._client is not None

        # Docker Hub service name (different from the registry hostname)
        service = "registry.docker.io" if "docker.io" in registry else registry

        params: dict[str, str] = {
            "service": service,
            "scope": scope,
        }
        auth: tuple[str, str] | None = None
        if self._username and self._password:
            auth = (self._username, self._password)

        logger.debug("Fetching Bearer token for scope: %s", scope)
        resp = await self._client.get(
            f"{self._auth_url}/token",
            params=params,
            auth=auth,
        )
        if resp.status_code != 200:
            raise RegistryError(
                f"Auth failed for {repository}",
                status_code=resp.status_code,
            )

        data = resp.json()
        token: str = data.get("token") or data.get("access_token", "")
        if not token:
            raise RegistryError("Registry returned empty auth token")

        self._tokens[scope] = token
        return token

    async def _authed_request(
        self,
        method: str,
        url: str,
        repository: str,
        registry: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """Make an authenticated HTTP request with Bearer token.

        Automatically fetches/caches the token for the given repository.

        Args:
            method:     HTTP method ("GET", "HEAD", etc.)
            url:        Full request URL.
            repository: OCI repository name (for token scoping).
            registry:   Registry host.
            **kwargs:   Extra arguments passed to httpx request.

        Returns:
            httpx.Response object.
        """
        assert self._client is not None
        token = await self._get_token(repository, registry)
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"

        resp = await self._client.request(method, url, headers=headers, **kwargs)
        return resp

    # ------------------------------------------------------------------
    # Manifest
    # ------------------------------------------------------------------

    async def get_manifest(
        self, image: str
    ) -> dict[str, Any]:
        """Fetch the OCI manifest for an image.

        Handles manifest lists (multi-arch) by selecting linux/amd64.

        Args:
            image: Full image reference, e.g. "ubuntu:24.04".

        Returns:
            Parsed manifest JSON as a dict.

        Raises:
            ImageNotFoundError: If the image/tag doesn't exist.
            RegistryError:      On non-404 HTTP errors.
        """
        registry, repository, tag = _parse_image_ref(image)
        url = f"https://{registry}/v2/{repository}/manifests/{tag}"

        resp = await self._authed_request(
            "GET",
            url,
            repository=repository,
            registry=registry,
            headers={"Accept": ACCEPT_MANIFESTS},
        )

        if resp.status_code == 404:
            raise ImageNotFoundError(image)
        if resp.status_code != 200:
            raise RegistryError(
                f"Failed to fetch manifest for {image}",
                status_code=resp.status_code,
            )

        manifest: dict[str, Any] = resp.json()
        media_type = manifest.get("mediaType", "") or resp.headers.get("content-type", "")

        # If this is a manifest list, resolve to linux/amd64
        if media_type in (MANIFEST_LIST, MANIFEST_OCI_INDEX) or "manifests" in manifest:
            manifest = await self._resolve_platform_manifest(
                manifest, repository, registry
            )

        return manifest

    async def _resolve_platform_manifest(
        self,
        manifest_list: dict[str, Any],
        repository: str,
        registry: str,
    ) -> dict[str, Any]:
        """From a manifest list, fetch the linux/amd64 manifest.

        Args:
            manifest_list: Parsed manifest list JSON.
            repository:    OCI repository name.
            registry:      Registry host.

        Returns:
            Resolved platform-specific manifest.

        Raises:
            RegistryError: If linux/amd64 is not found in the list.
        """
        for entry in manifest_list.get("manifests", []):
            platform = entry.get("platform", {})
            if platform.get("os") == "linux" and platform.get("architecture") == "amd64":
                digest = entry["digest"]
                url = f"https://{registry}/v2/{repository}/manifests/{digest}"
                resp = await self._authed_request(
                    "GET",
                    url,
                    repository=repository,
                    registry=registry,
                    headers={"Accept": ACCEPT_MANIFESTS},
                )
                if resp.status_code != 200:
                    raise RegistryError(
                        f"Failed to fetch platform manifest {digest}",
                        status_code=resp.status_code,
                    )
                return resp.json()  # type: ignore[no-any-return]

        raise RegistryError("No linux/amd64 manifest found in manifest list")

    # ------------------------------------------------------------------
    # Layers
    # ------------------------------------------------------------------

    async def pull_layer(
        self,
        repository: str,
        registry: str,
        digest: str,
        dest: Path,
    ) -> Path:
        """Download a single layer blob and verify its sha256 digest.

        Args:
            repository: OCI repository name (e.g. "library/ubuntu").
            registry:   Registry host.
            digest:     Layer digest, e.g. "sha256:abc123...".
            dest:       Directory to save the downloaded layer tarball.

        Returns:
            Path to the downloaded layer file.

        Raises:
            RegistryError: If download fails or digest verification fails.
        """
        dest.mkdir(parents=True, exist_ok=True)
        layer_file = dest / f"{digest.replace(':', '_')}.tar.gz"

        if layer_file.exists():
            # Layer already cached — skip download
            logger.debug("Layer already cached: %s", digest)
            return layer_file

        url = f"https://{registry}/v2/{repository}/blobs/{digest}"
        logger.debug("Downloading layer: %s", digest)

        sha256_hash = hashlib.sha256()

        async with self._client.stream(  # type: ignore[union-attr]
            "GET",
            url,
            headers={"Authorization": f"Bearer {await self._get_token(repository, registry)}"},
        ) as resp:
            if resp.status_code != 200:
                raise RegistryError(
                    f"Failed to download layer {digest}",
                    status_code=resp.status_code,
                )

            # Write to a temp file first, then rename atomically
            tmp_file = layer_file.with_suffix(".tmp")
            try:
                with open(tmp_file, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        # Update sha256 digest incrementally as data arrives
                        sha256_hash.update(chunk)
                        f.write(chunk)

                # Verify the downloaded content matches the expected digest
                expected_hex = digest.split(":")[-1]
                actual_hex = sha256_hash.hexdigest()
                if actual_hex != expected_hex:
                    raise RegistryError(
                        f"Layer digest mismatch for {digest}",
                        details=f"expected {expected_hex}, got {actual_hex}",
                    )

                # Atomic rename — layer_file is only created on success
                tmp_file.rename(layer_file)
                logger.debug("Layer downloaded and verified: %s", digest)

            except Exception:
                # Clean up partial download on any error
                tmp_file.unlink(missing_ok=True)
                raise

        return layer_file

    async def pull_image(
        self,
        image: str,
        images_dir: Path,
    ) -> tuple[dict[str, Any], list[Path]]:
        """Pull an image from the registry and save layers to disk.

        Downloads the manifest, then pulls each layer in parallel (up to
        4 concurrent downloads) and verifies sha256 digests.

        Args:
            image:      Image reference, e.g. "ubuntu:24.04".
            images_dir: Base directory for layer storage
                        (e.g. /var/lib/pybox/images).

        Returns:
            Tuple of (manifest_dict, list_of_layer_paths).

        Raises:
            ImageNotFoundError: If the image doesn't exist.
            RegistryError:      On download or verification failure.
        """
        manifest = await self.get_manifest(image)
        registry, repository, _ = _parse_image_ref(image)

        layers_dir = images_dir / "layers"
        layers_dir.mkdir(parents=True, exist_ok=True)

        layer_digests = [layer["digest"] for layer in manifest.get("layers", [])]

        # Download layers concurrently, but cap parallelism to avoid hammering
        # the registry or exhausting file descriptors.
        semaphore = asyncio.Semaphore(4)

        async def _pull_one(digest: str) -> Path:
            async with semaphore:
                return await self.pull_layer(repository, registry, digest, layers_dir)

        layer_paths = await asyncio.gather(*[_pull_one(d) for d in layer_digests])

        # Save manifest to disk
        import json
        image_digest = manifest.get("config", {}).get("digest", image.replace(":", "_"))
        safe_digest = image_digest.replace(":", "_").replace("/", "_")
        manifest_dir = images_dir / "sha256" / safe_digest
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

        logger.info(
            "Pulled image %s: %d layers", image, len(layer_paths)
        )
        return manifest, list(layer_paths)
