"""ImageBuilder: compile a BuildSpec into a layered OCI image.

Algorithm:
    1. Pull the base image (ImagePuller).
    2. For each build step:
       a. Compute the step's cache_key(parent_digest).
       b. If LayerCache HIT → reuse the cached layer tar, skip step execution.
       c. If MISS → run the step in an ephemeral container, snapshot the upper
          dir into a layer tar, store in LayerCache.
    3. If [python] section present → run PythonStepRunner as a virtual step.
    4. commit_image() assembles all layer tars into a valid OCI manifest + config.
    5. Tag the resulting digest in the tag index.

Ephemeral container lifecycle (per step):
    - Create a container with the accumulated layers so far as lowerdir.
    - Start it, run the step command.
    - Wait for it to exit.
    - Snapshot the upper dir.
    - Remove the ephemeral container.
"""

from __future__ import annotations

import logging
import json
from pathlib import Path
from typing import Any

from pybox.config import PyBoxConfig, get_config
from pybox.exceptions import ContainerError
from pybox.image.build_spec import (
    BuildSpec,
    BuildStep,
    CopyStep,
    EnvStep,
    RunStep,
    WorkdirStep,
)
from pybox.image.commit import commit_image
from pybox.image.puller import ImagePuller
from pybox.storage.layer_cache import LayerCache
from pybox.storage.snapshot import SnapshotManager

logger = logging.getLogger(__name__)


class ImageBuilder:
    """Builds OCI images from a BuildSpec using layer caching.

    Args:
        config:    PyBox global configuration.
        no_cache:  If True, ignore and bypass the layer cache.
    """

    def __init__(
        self,
        config: PyBoxConfig | None = None,
        no_cache: bool = False,
    ) -> None:
        self._cfg = config or get_config()
        self._no_cache = no_cache
        self._cache = LayerCache(self._cfg.root / "cache" / "layers")
        self._snapshots = SnapshotManager(self._cfg.containers_dir)

    async def build(self, spec: BuildSpec, tag: str) -> str:
        """Build an image from a BuildSpec and tag it.

        Args:
            spec: Validated BuildSpec from parse_boxfile().
            tag:  Image tag, e.g. "myapp:v1".

        Returns:
            Config digest (image ID), e.g. "sha256:abc123...".
        """
        logger.info("Building image %s from base %s", tag, spec.box.base)

        # Step 1: pull base image
        puller = ImagePuller(images_dir=self._cfg.images_dir)
        _, base_layer_dirs = await puller.pull(spec.box.base)

        # Load base image config for merge during commit
        base_config = self._load_base_config(spec.box.base)

        accumulated_layers: list[Path] = list(base_layer_dirs)
        all_layer_tars: list[Path] = list(self._existing_layer_tars(base_layer_dirs))
        parent_digest = spec.box.base  # used as parent for first step's cache key

        # Step 2: execute build steps
        for step in spec.steps:
            layer_tar, parent_digest = await self._execute_step(
                step, accumulated_layers, parent_digest
            )
            if layer_tar is not None:
                all_layer_tars.append(layer_tar)
                # Mount the new layer on top for the next step
                extracted = self._cfg.images_dir / "extracted" / layer_tar.stem
                extracted.mkdir(parents=True, exist_ok=True)
                accumulated_layers.append(extracted)

        # Step 3: Python environment step (if [python] section present)
        if spec.python is not None:
            layer_tar = await self._python_step(
                spec, accumulated_layers, parent_digest
            )
            if layer_tar is not None:
                all_layer_tars.append(layer_tar)

        # Step 4: commit all layers → OCI image
        digest = commit_image(
            layer_tars=all_layer_tars,
            base_config=base_config,
            build_spec=spec,
            tag=tag,
            images_dir=self._cfg.images_dir,
        )

        logger.info("Image %s built successfully: %s", tag, digest)
        return digest

    async def _execute_step(
        self,
        step: BuildStep,
        accumulated_layers: list[Path],
        parent_digest: str,
    ) -> tuple[Path | None, str]:
        """Execute one build step, using cache if available.

        Returns:
            Tuple of (layer_tar_path | None, new_parent_digest).
            layer_tar is None for steps that produce no filesystem changes
            (e.g. WorkdirStep, EnvStep which are metadata-only).
        """
        cache_key = step.cache_key(parent_digest)  # type: ignore[union-attr]
        step_name = getattr(step, "name", type(step).__name__)

        if isinstance(step, (WorkdirStep, EnvStep)):
            # Metadata-only steps: no layer diff needed
            logger.info("  [META] %s", step_name)
            return None, cache_key

        if not self._no_cache and self._cache.has(cache_key):
            logger.info("  [CACHE HIT] %s", step_name)
            return self._cache.get(cache_key), cache_key

        logger.info("  [BUILD] %s", step_name)

        if isinstance(step, RunStep):
            layer_tar = await self._run_step(step, accumulated_layers, cache_key, step_name)
        elif isinstance(step, CopyStep):
            layer_tar = await self._copy_step(step, accumulated_layers, cache_key, step_name)
        else:
            logger.warning("Unknown step type: %s", type(step).__name__)
            return None, cache_key

        self._cache.put(cache_key, layer_tar, description=step_name)
        return layer_tar, cache_key

    async def _run_step(
        self,
        step: RunStep,
        layers: list[Path],
        cache_key: str,
        step_name: str,
    ) -> Path:
        """Run a shell command in an ephemeral container and snapshot the diff."""
        from pybox.container.runtime import ContainerManager

        manager = ContainerManager(self._cfg)
        container_id = await manager.create(
            image="__ephemeral__",  # already have layer_dirs
            cmd=["/bin/sh", "-c", step.run],
        )

        # Override the overlay with our accumulated layers
        self._setup_ephemeral_rootfs(container_id, layers)

        try:
            manager.start(container_id)
            exit_code = manager.wait(container_id)
            if exit_code != 0:
                raise ContainerError(
                    f"Build step '{step_name}' failed with exit code {exit_code}"
                )
            return self._snapshots.snapshot(container_id)
        finally:
            try:
                manager.remove(container_id)
            except Exception:
                pass

    async def _copy_step(
        self,
        step: CopyStep,
        layers: list[Path],
        cache_key: str,
        step_name: str,
    ) -> Path:
        """Copy files from build context into a layer tar."""
        import shutil
        import tarfile
        import tempfile

        src = Path(step.files.src)
        dst = step.files.dst.lstrip("/")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dest_dir = tmp_path / "upper" / dst
            dest_dir.mkdir(parents=True, exist_ok=True)

            if src.is_dir():
                shutil.copytree(src, dest_dir / src.name, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dest_dir / src.name)

            # Pack the upper dir into a tar.gz
            tar_path = self._cfg.root / "tmp" / f"{cache_key}.tar.gz"
            tar_path.parent.mkdir(parents=True, exist_ok=True)
            with tarfile.open(tar_path, "w:gz") as tf:
                tf.add(tmp_path / "upper", arcname="")

        return tar_path

    async def _python_step(
        self,
        spec: BuildSpec,
        layers: list[Path],
        parent_digest: str,
    ) -> Path | None:
        """Install Python venv as a dedicated layer."""
        import hashlib
        import json
        from pybox.image.python_step import PythonStepRunner

        assert spec.python is not None
        cache_key = hashlib.sha256(
            json.dumps({
                "type": "python",
                "version": spec.python.version,
                "packages": sorted(spec.python.packages),
                "parent": parent_digest,
            }, sort_keys=True).encode()
        ).hexdigest()

        if not self._no_cache and self._cache.has(cache_key):
            logger.info("  [CACHE HIT] python env")
            return self._cache.get(cache_key)

        logger.info("  [BUILD] python env (version=%s, %d packages)",
                    spec.python.version, len(spec.python.packages))

        # We need a rootfs to run chroot in — use a temporary overlay
        from pybox.storage.overlay import prepare_container_overlay
        import uuid

        eph_id = uuid.uuid4().hex[:12]
        overlay = prepare_container_overlay(eph_id, layers, self._cfg.containers_dir)
        overlay.mount()
        try:
            runner = PythonStepRunner(overlay.merged_dir)
            runner.install_python_env(spec.python)
            tar_path = self._snapshots.snapshot(eph_id)
        finally:
            overlay.umount()
            try:
                import shutil
                shutil.rmtree(self._cfg.containers_dir / eph_id, ignore_errors=True)
            except Exception:
                pass

        self._cache.put(cache_key, tar_path, description="python env")
        return tar_path

    def _setup_ephemeral_rootfs(self, container_id: str, layers: list[Path]) -> None:
        """Override the rootfs for an ephemeral container with given layers."""
        from pybox.storage.overlay import prepare_container_overlay
        overlay = prepare_container_overlay(container_id, layers, self._cfg.containers_dir)
        # The container was created with a placeholder; re-mount with real layers
        try:
            overlay.umount()
        except Exception:
            pass
        overlay.mount()

        # Update container config to point to the new rootfs
        config_path = self._cfg.containers_dir / container_id / "config.json"
        if config_path.exists():
            data = json.loads(config_path.read_text())
            data["rootfs"] = str(overlay.merged_dir)
            config_path.write_text(json.dumps(data, indent=2))

    def _load_base_config(self, image_ref: str) -> dict[str, Any]:
        """Load the OCI config JSON for the base image if cached."""
        from pybox.registry.client import _parse_image_ref
        _, _, _ = _parse_image_ref(image_ref)  # noqa: unused
        # Walk image store looking for a config.json for this image
        sha256_dir = self._cfg.images_dir / "sha256"
        if not sha256_dir.exists():
            return {}
        for entry in sha256_dir.iterdir():
            config_file = entry / "config.json"
            if config_file.exists():
                try:
                    return json.loads(config_file.read_text())
                except Exception:
                    continue
        return {}

    def _existing_layer_tars(self, layer_dirs: list[Path]) -> list[Path]:
        """Find the original layer tarballs for already-extracted layers."""
        layers_store = self._cfg.images_dir / "layers"
        tars: list[Path] = []
        for layer_dir in layer_dirs:
            # Try to find a matching tar by digest name convention
            name = layer_dir.name
            tar = layers_store / f"{name}.tar.gz"
            if tar.exists():
                tars.append(tar)
        return tars
