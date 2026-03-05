"""Layer cache for build step outputs.

Maps step cache keys (sha256 of step content + parent digest) to pre-built
layer tar files. When a build step's cache key matches an existing entry, the
step is skipped and the cached layer is reused — identical to Docker's layer
caching behaviour.

Storage layout:
    <PYBOX_ROOT>/cache/layers/<step_hash>/
        layer.tar.gz    — the layer diff tarball
        meta.json       — step metadata (created_at, description)
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class LayerCache:
    """Disk-backed cache for build layer tarballs.

    Args:
        cache_dir: Root cache directory (e.g. <PYBOX_ROOT>/cache/layers).
    """

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _entry_dir(self, step_hash: str) -> Path:
        return self._cache_dir / step_hash

    def has(self, step_hash: str) -> bool:
        """Return True if a cached layer exists for this step hash."""
        return (self._entry_dir(step_hash) / "layer.tar.gz").exists()

    def get(self, step_hash: str) -> Path:
        """Return the path to the cached layer tarball.

        Args:
            step_hash: Cache key (sha256 hex string).

        Returns:
            Path to the layer.tar.gz file.

        Raises:
            KeyError: If the entry does not exist.
        """
        path = self._entry_dir(step_hash) / "layer.tar.gz"
        if not path.exists():
            raise KeyError(f"No cached layer for step hash: {step_hash}")
        logger.debug("Cache HIT: %s", step_hash[:16])
        return path

    def put(self, step_hash: str, layer_tar: Path, description: str = "") -> Path:
        """Store a layer tarball in the cache.

        Args:
            step_hash:   Cache key (sha256 hex string).
            layer_tar:   Path to the layer tarball to cache.
            description: Human-readable description for the meta file.

        Returns:
            Path to the cached copy of the layer tarball.
        """
        entry_dir = self._entry_dir(step_hash)
        entry_dir.mkdir(parents=True, exist_ok=True)

        dest = entry_dir / "layer.tar.gz"
        shutil.copy2(layer_tar, dest)

        meta = {
            "step_hash": step_hash,
            "description": description,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "size_bytes": dest.stat().st_size,
        }
        (entry_dir / "meta.json").write_text(json.dumps(meta, indent=2))

        logger.debug("Cache PUT: %s (%d bytes)", step_hash[:16], dest.stat().st_size)
        return dest

    def evict(self, step_hash: str) -> None:
        """Remove a cached layer entry."""
        entry_dir = self._entry_dir(step_hash)
        if entry_dir.exists():
            shutil.rmtree(entry_dir)
            logger.debug("Cache EVICT: %s", step_hash[:16])

    def clear(self) -> None:
        """Remove all cached layers."""
        if self._cache_dir.exists():
            shutil.rmtree(self._cache_dir)
            self._cache_dir.mkdir(parents=True)
        logger.info("Layer cache cleared")

    def stats(self) -> dict[str, int]:
        """Return cache statistics."""
        entries = [e for e in self._cache_dir.iterdir() if e.is_dir()] if self._cache_dir.exists() else []
        total_size = sum(
            f.stat().st_size
            for e in entries
            for f in e.rglob("*")
            if f.is_file()
        )
        return {"entries": len(entries), "total_bytes": total_size}
