"""OverlayFS mount/umount for container root filesystems.

OverlayFS is a union filesystem that merges multiple "lower" read-only
directories (image layers) with an "upper" read-write directory (container
writes) into a single "merged" view.

Mount command structure:
    mount -t overlay overlay \\
        -o lowerdir=<l1>:<l2>:...,upperdir=<upper>,workdir=<work> \\
        <merged>

Notes:
    - lowerdir is a colon-separated list; rightmost directory is the TOP layer.
    - upperdir captures all writes from inside the container.
    - workdir must be on the same filesystem as upperdir (used internally by
      the kernel for atomic operations).
    - subprocess is used here for the mount/umount commands — this is the
      approved exception for mount operations per ARCHITECTURE.md.

Reference: https://www.kernel.org/doc/html/latest/filesystems/overlayfs.html
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from pybox.exceptions import StorageError

logger = logging.getLogger(__name__)


class OverlayMount:
    """Manages an OverlayFS mount for a single container.

    Attributes:
        container_id: Short container identifier (12 hex chars).
        layer_dirs:   Ordered list of read-only layer directories (base first).
        upper_dir:    Read-write upper directory for container modifications.
        work_dir:     OverlayFS work directory (must be on same FS as upper).
        merged_dir:   Mount point where the merged filesystem appears.
    """

    def __init__(
        self,
        container_id: str,
        layer_dirs: list[Path],
        upper_dir: Path,
        work_dir: Path,
        merged_dir: Path,
    ) -> None:
        self.container_id = container_id
        self.layer_dirs = layer_dirs
        self.upper_dir = upper_dir
        self.work_dir = work_dir
        self.merged_dir = merged_dir
        self._mounted = False

    def mount(self) -> None:
        """Mount the OverlayFS.

        Creates upper, work, and merged directories if they don't exist,
        then calls mount(8) via subprocess.

        Raises:
            StorageError: If the mount command fails or no layers are provided.
        """
        if not self.layer_dirs:
            raise StorageError(
                f"Cannot mount OverlayFS for {self.container_id}: no layer directories provided"
            )

        # Ensure all required directories exist before mounting
        self.upper_dir.mkdir(parents=True, exist_ok=True)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.merged_dir.mkdir(parents=True, exist_ok=True)

        # OverlayFS lowerdir: colon-separated, rightmost = topmost layer.
        # We receive layers in base-first order, so reverse for OverlayFS.
        # Example: layers [base, layer1, layer2] → lowerdir=layer2:layer1:base
        lowerdir = ":".join(str(d) for d in reversed(self.layer_dirs))

        # Build the mount options string
        options = (
            f"lowerdir={lowerdir},"
            f"upperdir={self.upper_dir},"
            f"workdir={self.work_dir}"
        )

        logger.debug(
            "Mounting OverlayFS for %s: merged=%s", self.container_id, self.merged_dir
        )

        try:
            # mount -t overlay overlay -o <options> <merged>
            # Using subprocess as approved for mount operations.
            subprocess.run(
                [
                    "mount",
                    "-t", "overlay",
                    "overlay",
                    "-o", options,
                    str(self.merged_dir),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise StorageError(
                f"Failed to mount OverlayFS for container {self.container_id}",
                details=exc.stderr.strip(),
            ) from exc

        self._mounted = True
        logger.info("OverlayFS mounted at %s", self.merged_dir)

    def umount(self) -> None:
        """Unmount the OverlayFS with lazy unmount (MNT_DETACH).

        Lazy unmount (-l) detaches the mount immediately but keeps it alive
        until all open file descriptors are closed. This is safe to call
        even while processes inside the container may still have files open.

        Does nothing if the filesystem is not currently mounted.
        """
        if not self._mounted:
            return

        logger.debug("Unmounting OverlayFS at %s", self.merged_dir)

        try:
            # umount -l performs a lazy detach — safe even with open FDs
            subprocess.run(
                ["umount", "-l", str(self.merged_dir)],
                check=True,
                capture_output=True,
                text=True,
            )
            self._mounted = False
            logger.info("OverlayFS unmounted: %s", self.merged_dir)
        except subprocess.CalledProcessError as exc:
            logger.warning(
                "Failed to unmount %s: %s", self.merged_dir, exc.stderr.strip()
            )

    def __enter__(self) -> "OverlayMount":
        self.mount()
        return self

    def __exit__(self, *_: object) -> None:
        self.umount()


class VFSCopyMount:
    """Fallback storage driver: merges layers by copying into a single directory.

    No copy-on-write semantics — upper layers simply overwrite files from lower
    layers. Works everywhere without root or FUSE. Used when neither kernel
    OverlayFS nor fuse-overlayfs is available.

    umount() is a no-op; the merged directory persists until the container is
    removed by ContainerManager.remove().
    """

    def __init__(
        self,
        container_id: str,
        layer_dirs: list[Path],
        upper_dir: Path,
        merged_dir: Path,
    ) -> None:
        self.container_id = container_id
        self.layer_dirs = layer_dirs
        self.upper_dir = upper_dir
        self.merged_dir = merged_dir
        self._mounted = False

    def mount(self) -> None:
        """Merge layers by copying, base → top, into merged_dir."""
        self.upper_dir.mkdir(parents=True, exist_ok=True)
        self.merged_dir.mkdir(parents=True, exist_ok=True)

        # Copy layers base-first so upper layers overwrite lower ones
        for layer_dir in self.layer_dirs:
            if layer_dir.exists():
                shutil.copytree(layer_dir, self.merged_dir, dirs_exist_ok=True)

        self._mounted = True
        logger.info("VFS copy mount ready at %s", self.merged_dir)

    def umount(self) -> None:
        """No-op — VFS copy mount does not have a kernel mount to release."""
        self._mounted = False

    def __enter__(self) -> "VFSCopyMount":
        self.mount()
        return self

    def __exit__(self, *_: object) -> None:
        self.umount()


def prepare_container_overlay(
    container_id: str,
    layer_dirs: list[Path],
    containers_dir: Path,
) -> "OverlayMount | VFSCopyMount":
    """Create and return the best available overlay driver for a container.

    Selection order:
        1. Kernel OverlayFS  — when running as root (uid == 0)
        2. fuse-overlayfs    — when rootless and fuse-overlayfs is in PATH
        3. VFS copy fallback — always available, no COW semantics

    Args:
        container_id:   Short container ID (12 hex chars).
        layer_dirs:     Ordered layer directories (base layer first).
        containers_dir: Root of the container storage.

    Returns:
        A mount driver instance (not yet mounted).
    """
    from pybox.rootless.fuse_overlay import FuseOverlayFSDriver

    container_dir = containers_dir / container_id
    upper_dir = container_dir / "upper"
    work_dir = container_dir / "work"
    merged_dir = container_dir / "rootfs"

    if os.getuid() == 0:
        # Root: use kernel OverlayFS
        return OverlayMount(
            container_id=container_id,
            layer_dirs=layer_dirs,
            upper_dir=upper_dir,
            work_dir=work_dir,
            merged_dir=merged_dir,
        )

    if FuseOverlayFSDriver.check_available():
        # Rootless + fuse-overlayfs installed
        logger.debug("Using fuse-overlayfs for rootless container %s", container_id)
        return FuseOverlayFSDriver(
            container_id=container_id,
            layer_dirs=layer_dirs,
            upper_dir=upper_dir,
            work_dir=work_dir,
            merged_dir=merged_dir,
        )

    # Rootless fallback: plain directory copy (no COW)
    logger.warning(
        "fuse-overlayfs not found — using VFS copy fallback for container %s. "
        "Install fuse-overlayfs for proper copy-on-write support: "
        "sudo apt install fuse-overlayfs",
        container_id,
    )
    return VFSCopyMount(
        container_id=container_id,
        layer_dirs=layer_dirs,
        upper_dir=upper_dir,
        merged_dir=merged_dir,
    )
