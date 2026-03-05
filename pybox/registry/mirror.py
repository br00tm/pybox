"""Registry pull-through mirror/cache.

Intercepts layer download requests and serves them from a local cache,
falling back to the upstream registry on a miss. Reduces redundant
downloads in CI environments and low-bandwidth situations.

Cache layout:
    <PYBOX_ROOT>/mirror/<registry>/<repo>/<digest>/blob

Configured via PYBOX_REGISTRY_MIRROR environment variable.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

import httpx

from pybox.exceptions import RegistryError

logger = logging.getLogger(__name__)


class RegistryMirror:
    """Local cache for OCI blob downloads.

    Args:
        mirror_dir: Root of the mirror cache (e.g. <PYBOX_ROOT>/mirror).
    """

    def __init__(self, mirror_dir: Path) -> None:
        self._mirror_dir = mirror_dir
        self._mirror_dir.mkdir(parents=True, exist_ok=True)

    def _blob_path(self, registry: str, repository: str, digest: str) -> Path:
        """Return the local cache path for a blob."""
        safe_registry = registry.replace(":", "_")
        safe_repo = repository.replace("/", "_")
        safe_digest = digest.replace(":", "_")
        return self._mirror_dir / safe_registry / safe_repo / safe_digest / "blob"

    def has(self, registry: str, repository: str, digest: str) -> bool:
        """Return True if the blob is cached locally."""
        return self._blob_path(registry, repository, digest).exists()

    def get(self, registry: str, repository: str, digest: str) -> Path:
        """Return the local path to a cached blob.

        Raises:
            KeyError: If the blob is not in cache.
        """
        path = self._blob_path(registry, repository, digest)
        if not path.exists():
            raise KeyError(f"Blob not in mirror: {digest}")
        return path

    async def fetch(
        self,
        registry: str,
        repository: str,
        digest: str,
        token: str,
    ) -> Path:
        """Fetch a blob from the upstream registry and cache it.

        Args:
            registry:   Registry hostname.
            repository: OCI repository name.
            digest:     Content digest.
            token:      Bearer token for authentication.

        Returns:
            Path to the locally cached blob file.

        Raises:
            RegistryError: If the download fails or digest verification fails.
        """
        if self.has(registry, repository, digest):
            logger.debug("Mirror HIT: %s", digest[:20])
            return self.get(registry, repository, digest)

        logger.debug("Mirror MISS: %s — fetching from %s", digest[:20], registry)
        path = self._blob_path(registry, repository, digest)
        path.parent.mkdir(parents=True, exist_ok=True)

        url = f"https://{registry}/v2/{repository}/blobs/{digest}"
        sha = hashlib.sha256()

        tmp = path.with_suffix(".tmp")
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=300.0) as client:
                async with client.stream(
                    "GET", url, headers={"Authorization": f"Bearer {token}"}
                ) as resp:
                    if resp.status_code != 200:
                        raise RegistryError(
                            f"Mirror: failed to fetch {digest}", status_code=resp.status_code
                        )
                    with open(tmp, "wb") as f:
                        async for chunk in resp.aiter_bytes(65536):
                            sha.update(chunk)
                            f.write(chunk)

            expected = digest.split(":")[-1]
            if sha.hexdigest() != expected:
                raise RegistryError(f"Mirror: digest mismatch for {digest}")

            tmp.rename(path)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

        logger.debug("Cached blob: %s (%d bytes)", digest[:20], path.stat().st_size)
        return path

    def evict(self, registry: str, repository: str, digest: str) -> None:
        """Remove a blob from the cache."""
        path = self._blob_path(registry, repository, digest)
        path.unlink(missing_ok=True)

    def stats(self) -> dict[str, int]:
        """Return cache statistics (total blobs and bytes)."""
        blobs = list(self._mirror_dir.rglob("blob"))
        total_size = sum(b.stat().st_size for b in blobs)
        return {"blobs": len(blobs), "total_bytes": total_size}
