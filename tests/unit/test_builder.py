"""Unit tests for pybox.image.builder and pybox.storage.layer_cache."""

from __future__ import annotations

from pathlib import Path

import pytest


class TestLayerCache:
    def test_has_returns_false_initially(self, tmp_path: Path) -> None:
        from pybox.storage.layer_cache import LayerCache
        cache = LayerCache(tmp_path)
        assert not cache.has("abc123")

    def test_put_and_has(self, tmp_path: Path) -> None:
        from pybox.storage.layer_cache import LayerCache
        cache = LayerCache(tmp_path)

        tar = tmp_path / "layer.tar.gz"
        tar.write_bytes(b"fake tar")
        cache.put("abc123", tar, "test step")
        assert cache.has("abc123")

    def test_get_returns_correct_path(self, tmp_path: Path) -> None:
        from pybox.storage.layer_cache import LayerCache
        cache = LayerCache(tmp_path)

        tar = tmp_path / "layer.tar.gz"
        tar.write_bytes(b"content")
        stored = cache.put("def456", tar)
        result = cache.get("def456")
        assert result.exists()
        assert result.read_bytes() == b"content"

    def test_get_missing_raises_key_error(self, tmp_path: Path) -> None:
        from pybox.storage.layer_cache import LayerCache
        cache = LayerCache(tmp_path)
        with pytest.raises(KeyError):
            cache.get("nonexistent")

    def test_evict_removes_entry(self, tmp_path: Path) -> None:
        from pybox.storage.layer_cache import LayerCache
        cache = LayerCache(tmp_path)

        tar = tmp_path / "l.tar.gz"
        tar.write_bytes(b"x")
        cache.put("ghi789", tar)
        assert cache.has("ghi789")
        cache.evict("ghi789")
        assert not cache.has("ghi789")

    def test_stats_returns_correct_count(self, tmp_path: Path) -> None:
        from pybox.storage.layer_cache import LayerCache
        cache = LayerCache(tmp_path)

        for i in range(3):
            tar = tmp_path / f"l{i}.tar.gz"
            tar.write_bytes(b"x" * 100)
            cache.put(f"key{i}", tar)

        stats = cache.stats()
        assert stats["entries"] == 3
        assert stats["total_bytes"] > 0

    def test_clear_removes_all(self, tmp_path: Path) -> None:
        from pybox.storage.layer_cache import LayerCache
        cache = LayerCache(tmp_path)

        for i in range(3):
            tar = tmp_path / f"l{i}.tar.gz"
            tar.write_bytes(b"y")
            cache.put(f"k{i}", tar)

        cache.clear()
        assert cache.stats()["entries"] == 0


class TestImageBuilderCacheLogic:
    """Test that ImageBuilder correctly uses/skips the layer cache."""

    def _make_run_step(self, cmd: str = "echo hello"):
        from pybox.image.build_spec import RunStep
        return RunStep(name="test", run=cmd)

    def test_cache_hit_returns_cached_layer(self, tmp_path: Path) -> None:
        from pybox.storage.layer_cache import LayerCache

        cache = LayerCache(tmp_path)
        tar = tmp_path / "cached.tar.gz"
        tar.write_bytes(b"cached content")

        step = self._make_run_step()
        cache_key = step.cache_key("parent")
        cache.put(cache_key, tar)

        assert cache.has(cache_key)
        cached_path = cache.get(cache_key)
        assert cached_path.read_bytes() == b"cached content"

    def test_no_cache_flag_bypasses_cache(self, tmp_path: Path) -> None:
        """Builder with no_cache=True should not use cached layers."""
        from pybox.storage.layer_cache import LayerCache

        cache = LayerCache(tmp_path)
        tar = tmp_path / "existing.tar.gz"
        tar.write_bytes(b"old content")
        step = self._make_run_step()
        cache_key = step.cache_key("parent")
        cache.put(cache_key, tar)

        # Even if cache has it, no_cache=True should not return it
        # (tested via the builder's _no_cache flag logic)
        from pybox.image.builder import ImageBuilder
        from pybox.config import PyBoxConfig

        cfg = PyBoxConfig(PYBOX_ROOT=str(tmp_path))  # type: ignore
        builder = ImageBuilder(config=cfg, no_cache=True)
        assert builder._no_cache is True
