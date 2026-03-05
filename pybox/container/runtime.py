"""Container lifecycle management: create, start, wait, stop, remove.

ContainerManager orchestrates:
    1. Image pulling (ImagePuller)
    2. OverlayFS setup (OverlayMount)
    3. cgroups v2 resource limits (CgroupV2)
    4. Namespace isolation and process spawn (os.fork + container_init)
    5. State persistence (StateManager)
    6. Cleanup on stop/remove

Typical flow:
    manager = ContainerManager(config)
    container_id = await manager.create("ubuntu:24.04", ["/bin/bash"])
    await manager.start(container_id)
    await manager.wait(container_id)
    await manager.remove(container_id)
"""

from __future__ import annotations

import logging
import os
import signal
from pathlib import Path
from typing import Any

from pybox.cgroups.specs import CgroupSpec
from pybox.cgroups.v2 import CgroupV2
from pybox.config import PyBoxConfig, get_config
from pybox.container.config import ContainerConfig
from pybox.container.state import ContainerState, StateManager
from pybox.exceptions import ContainerError, ContainerNotFoundError
from pybox.image.puller import ImagePuller
from pybox.storage.overlay import prepare_container_overlay

logger = logging.getLogger(__name__)


class ContainerManager:
    """Manages the full lifecycle of PyBox containers.

    Each ContainerManager instance is bound to a PyBoxConfig that determines
    storage paths and cgroup roots. Multiple instances may coexist but should
    operate on different containers.

    Args:
        config: PyBox global configuration. Defaults to the process-wide config.
    """

    def __init__(self, config: PyBoxConfig | None = None) -> None:
        self._cfg = config or get_config()
        self._state = StateManager(self._cfg.containers_dir)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create(
        self,
        image: str,
        cmd: list[str],
        *,
        name: str | None = None,
        env: dict[str, str] | None = None,
        volumes: list[str] | None = None,
        hostname: str | None = None,
        cgroup_spec: CgroupSpec | None = None,
        network_mode: str = "bridge",
    ) -> str:
        """Pull the image (if needed), prepare storage, and register the container.

        Args:
            image:        Image reference, e.g. "ubuntu:24.04".
            cmd:          Command to run inside the container.
            name:         Optional human-readable name.
            env:          Extra environment variables.
            volumes:      Volume bind-mount specs ("src:dst").
            hostname:     Hostname for the UTS namespace.
            cgroup_spec:  Resource limits.
            network_mode: Network mode ("bridge", "host", "none").

        Returns:
            The newly created container ID (12-char hex).

        Raises:
            ContainerError: If image pull or storage setup fails.
        """
        container_cfg = ContainerConfig(
            image=image,
            cmd=cmd,
            name=name,
            env=env or {},
            volumes=volumes or [],
            hostname=hostname,
            cgroup_spec=cgroup_spec,
            network_mode=network_mode,
        )
        container_id = container_cfg.id

        try:
            # Pull image and prepare OverlayFS rootfs
            layer_dirs = await self._pull_image(image)
            rootfs = self._prepare_storage(container_id, layer_dirs)
            container_cfg = container_cfg.model_copy(update={"rootfs": rootfs})

            # Persist config and initial state
            self._cfg.containers_dir.mkdir(parents=True, exist_ok=True)
            container_dir = self._cfg.containers_dir / container_id
            container_dir.mkdir(parents=True, exist_ok=True)

            import json
            (container_dir / "config.json").write_text(
                json.dumps(container_cfg.model_dump(mode="json"), indent=2)
            )

            self._state.create(
                container_id,
                {
                    "state": ContainerState.CREATED.value,
                    "image": image,
                    "cmd": cmd,
                    "name": name,
                    "pid": None,
                    "rootfs": str(rootfs),
                },
            )

            logger.info("Container %s created (image=%s)", container_id, image)
            return container_id

        except ContainerError:
            raise
        except Exception as exc:
            raise ContainerError(
                f"Failed to create container from image '{image}'", details=str(exc)
            ) from exc

    def start(self, container_id: str) -> int:
        """Fork and start the container process.

        The child process runs container_init() which unshares namespaces,
        pivots root, and execs the user command.

        Args:
            container_id: Container ID returned by create().

        Returns:
            PID of the container's init process.

        Raises:
            ContainerNotFoundError: If the container doesn't exist.
            ContainerError: If fork fails or init setup fails.
        """
        state = self._state.get(container_id)
        if state["state"] not in (ContainerState.CREATED.value, ContainerState.STOPPED.value):
            raise ContainerError(
                f"Container {container_id} is in state '{state['state']}'; cannot start"
            )

        import json
        config_path = self._cfg.containers_dir / container_id / "config.json"
        if not config_path.exists():
            raise ContainerNotFoundError(container_id)

        container_cfg_data: dict[str, Any] = json.loads(config_path.read_text())
        container_cfg = ContainerConfig.model_validate(container_cfg_data)

        # Apply cgroup limits before fork (best-effort — skipped when rootless)
        cgroup = CgroupV2(container_id, self._cfg.cgroup_root)
        _cgroup_available = False
        try:
            cgroup.create()
            if container_cfg.cgroup_spec:
                cgroup.apply_limits(container_cfg.cgroup_spec)
            _cgroup_available = True
        except Exception as exc:
            logger.warning(
                "cgroup setup skipped for %s (running rootless or no permission): %s",
                container_id, exc,
            )

        # Two-pipe synchronisation for user-namespace uid/gid map handshake:
        #   child_ready:  child → parent  (child signals after unshare NEWUSER)
        #   maps_done:    parent → child  (parent signals after writing maps)
        child_ready_r, child_ready_w = os.pipe()
        maps_done_r, maps_done_w = os.pipe()

        # Fork: child runs container_init, parent orchestrates uid/gid maps
        pid = os.fork()
        if pid == 0:
            # ---- Child process ----
            os.close(child_ready_r)
            os.close(maps_done_w)
            from pybox.container.init import container_init
            container_init(container_cfg, child_ready_w=child_ready_w, maps_done_r=maps_done_r)
            os._exit(1)  # noqa: SLF001

        # ---- Parent process ----
        os.close(child_ready_w)
        os.close(maps_done_r)

        # Wait for child to signal its user namespace is ready
        try:
            os.read(child_ready_r, 1)
        finally:
            os.close(child_ready_r)

        # Write uid/gid maps for the child's new user namespace
        self._write_id_maps_for_child(pid)

        # Signal child: maps are written, continue with rest of setup
        os.write(maps_done_w, b"\x00")
        os.close(maps_done_w)

        # Add the child to the cgroup so resource limits apply
        if _cgroup_available:
            try:
                cgroup.add_pid(pid)
            except Exception as exc:
                logger.warning("Failed to add PID %d to cgroup: %s", pid, exc)

        self._state.update(container_id, state=ContainerState.RUNNING.value, pid=pid)
        logger.info("Container %s started (PID=%d)", container_id, pid)
        return pid

    def wait(self, container_id: str) -> int:
        """Wait for the container process to exit and return its exit code.

        Args:
            container_id: Container ID.

        Returns:
            Exit status code from the container process.

        Raises:
            ContainerNotFoundError: If the container doesn't exist.
            ContainerError: If the container is not running.
        """
        state = self._state.get(container_id)
        pid: int | None = state.get("pid")

        if pid is None:
            raise ContainerError(f"Container {container_id} has no associated PID")

        logger.debug("Waiting for container %s (PID=%d)", container_id, pid)

        try:
            # os.waitpid blocks until the child process exits
            _, wait_status = os.waitpid(pid, 0)
            exit_code = os.waitstatus_to_exitcode(wait_status)
        except ChildProcessError:
            # Process already reaped
            exit_code = 0

        self._state.update(
            container_id,
            state=ContainerState.STOPPED.value,
            pid=None,
            exit_code=exit_code,
        )
        logger.info("Container %s exited with code %d", container_id, exit_code)
        return exit_code

    def stop(self, container_id: str, timeout: int = 10) -> None:
        """Send SIGTERM to the container, then SIGKILL after timeout.

        Args:
            container_id: Container ID.
            timeout:      Seconds to wait for graceful shutdown before SIGKILL.

        Raises:
            ContainerNotFoundError: If the container doesn't exist.
        """
        state = self._state.get(container_id)
        pid: int | None = state.get("pid")

        if state["state"] != ContainerState.RUNNING.value or pid is None:
            logger.debug("Container %s is not running; nothing to stop", container_id)
            return

        logger.info("Stopping container %s (PID=%d, timeout=%ds)", container_id, pid, timeout)

        try:
            # Send SIGTERM for graceful shutdown
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            # Process already dead
            self._state.set_state(container_id, ContainerState.STOPPED)
            return

        # Wait up to timeout seconds, then SIGKILL
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                result, _ = os.waitpid(pid, os.WNOHANG)
                if result != 0:
                    self._state.set_state(container_id, ContainerState.STOPPED)
                    return
            except ChildProcessError:
                self._state.set_state(container_id, ContainerState.STOPPED)
                return
            time.sleep(0.1)

        # Timeout expired — force kill
        try:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        except (ProcessLookupError, ChildProcessError):
            pass

        self._state.set_state(container_id, ContainerState.STOPPED)
        logger.info("Container %s killed", container_id)

    def remove(self, container_id: str) -> None:
        """Clean up all resources for a stopped container.

        Removes the OverlayFS mount, cgroup, and state files.

        Args:
            container_id: Container ID.

        Raises:
            ContainerError: If the container is still running.
        """
        state = self._state.get(container_id)
        if state["state"] == ContainerState.RUNNING.value:
            raise ContainerError(
                f"Container {container_id} is running; stop it before removing"
            )

        # Unmount OverlayFS (lazy unmount — safe even if still referenced)
        rootfs = state.get("rootfs")
        if rootfs:
            self._cleanup_overlay(container_id, Path(rootfs))

        # Remove the cgroup directory
        cgroup = CgroupV2(container_id, self._cfg.cgroup_root)
        try:
            cgroup.delete()
        except Exception as exc:
            logger.warning("Failed to delete cgroup for %s: %s", container_id, exc)

        # Remove state directory (config.json, state.json, upper/, work/, rootfs/)
        self._state.remove(container_id)
        logger.info("Container %s removed", container_id)

    def list_containers(self, all_states: bool = False) -> list[dict[str, Any]]:
        """List containers, optionally including non-running ones.

        Args:
            all_states: If True, include stopped/created containers too.

        Returns:
            List of state dicts.
        """
        containers = self._state.list_all()
        if not all_states:
            containers = [c for c in containers if c.get("state") == ContainerState.RUNNING.value]
        return containers

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write_id_maps_for_child(self, pid: int) -> None:
        """Write uid_map and gid_map for the child's new user namespace.

        Tries (in order):
        1. newuidmap/newgidmap setuid binaries — work for any unprivileged user
           with /etc/subuid entries; most reliable rootless approach.
        2. Direct /proc/<pid>/{uid,gid}_map write — works when parent has
           CAP_SETUID (root) or is writing a single-entry own-UID mapping.

        Called from the parent after the child signals its user namespace is
        ready (CLONE_NEWUSER done) and before signalling maps_done.
        """
        import shutil
        import subprocess

        uid = os.getuid()
        gid = os.getgid()

        newuidmap = shutil.which("newuidmap")
        newgidmap = shutil.which("newgidmap")

        if newuidmap and newgidmap:
            try:
                subprocess.run(
                    [newuidmap, str(pid), "0", str(uid), "1"],
                    check=True, capture_output=True, text=True,
                )
                subprocess.run(
                    [newgidmap, str(pid), "0", str(gid), "1"],
                    check=True, capture_output=True, text=True,
                )
                logger.debug("newuidmap/newgidmap wrote maps for PID %d", pid)
                return
            except subprocess.CalledProcessError as exc:
                logger.debug("newuidmap failed (%s), falling back to direct write", exc.stderr.strip())

        # Direct write fallback
        from pybox.namespace.user_map import IDMapping, write_uid_map, write_gid_map
        try:
            write_uid_map(pid, [IDMapping(container_id=0, host_id=uid, count=1)])
            write_gid_map(pid, [IDMapping(container_id=0, host_id=gid, count=1)])
            logger.debug("Direct write of uid/gid maps for PID %d (host uid=%d)", pid, uid)
        except Exception as exc:
            logger.warning("Failed to write id maps for child %d: %s — container may lack root inside", pid, exc)

    async def _pull_image(self, image: str) -> list[Path]:
        """Pull an image and return its extracted layer directories."""
        puller = ImagePuller(images_dir=self._cfg.images_dir)
        _, layer_dirs = await puller.pull(image)
        return layer_dirs

    def _prepare_storage(self, container_id: str, layer_dirs: list[Path]) -> Path:
        """Mount OverlayFS and return the merged rootfs path."""
        overlay = prepare_container_overlay(
            container_id, layer_dirs, self._cfg.containers_dir
        )
        overlay.mount()
        return overlay.merged_dir

    def _cleanup_overlay(self, container_id: str, rootfs: Path) -> None:
        """Attempt to unmount the overlay for a container.

        Tries kernel umount first (root), then fusermount (fuse-overlayfs),
        then silently skips (VFS copy or already unmounted).
        """
        import shutil
        import subprocess

        # Try kernel umount (works when root or mount was kernel OverlayFS)
        try:
            subprocess.run(
                ["umount", "-l", str(rootfs)],
                check=True,
                capture_output=True,
                text=True,
            )
            logger.debug("Unmounted overlay for container %s", container_id)
            return
        except subprocess.CalledProcessError:
            pass

        # Try fusermount (works for fuse-overlayfs rootless mounts)
        fusermount = shutil.which("fusermount3") or shutil.which("fusermount")
        if fusermount:
            try:
                subprocess.run(
                    [fusermount, "-u", str(rootfs)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                logger.debug("Fusermount unmounted overlay for container %s", container_id)
                return
            except subprocess.CalledProcessError:
                pass

        # VFS copy mount or already unmounted — nothing to do
        logger.debug("No active mount to clean up for container %s", container_id)
