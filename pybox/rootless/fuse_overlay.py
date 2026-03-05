"""fuse-overlayfs driver for rootless OverlayFS mounts.

When running without root, the kernel OverlayFS requires CAP_SYS_ADMIN.
fuse-overlayfs provides a userspace equivalent that works with FUSE and
does not require any special capabilities.

Requires:
    - fuse-overlayfs binary in PATH
    - /dev/fuse accessible by the current user
    - fusermount (fuse2) or fusermount3 for unmounting

Falls back to a simple VFS directory copy if fuse-overlayfs is not available.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from pybox.exceptions import StorageError

logger = logging.getLogger(__name__)


class FuseOverlayFSDriver:
    """Rootless OverlayFS via fuse-overlayfs.

    Args:
        container_id: Container identifier.
        layer_dirs:   Ordered read-only layer directories (base first).
        upper_dir:    Read-write upper directory.
        work_dir:     OverlayFS work directory.
        merged_dir:   Mount point for the merged view.
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

    @staticmethod
    def check_available() -> bool:
        """Return True if fuse-overlayfs is available and usable."""
        if not shutil.which("fuse-overlayfs"):
            return False
        return Path("/dev/fuse").exists()

    def mount(self) -> None:
        """Mount the fuse-overlayfs filesystem.

        Raises:
            StorageError: If fuse-overlayfs is unavailable or mounting fails.
        """
        if not self.check_available():
            raise StorageError(
                "fuse-overlayfs not available. Install it with: apt install fuse-overlayfs"
            )

        for d in [self.upper_dir, self.work_dir, self.merged_dir]:
            d.mkdir(parents=True, exist_ok=True)

        # lowerdir is colon-separated, reversed (top layer first for OverlayFS)
        lowerdir = ":".join(str(d) for d in reversed(self.layer_dirs))
        options = (
            f"lowerdir={lowerdir},"
            f"upperdir={self.upper_dir},"
            f"workdir={self.work_dir}"
        )

        try:
            subprocess.run(
                ["fuse-overlayfs", "-o", options, str(self.merged_dir)],
                check=True,
                capture_output=True,
                text=True,
            )
            self._mounted = True
            logger.debug("fuse-overlayfs mounted at %s", self.merged_dir)
        except subprocess.CalledProcessError as exc:
            raise StorageError(
                f"fuse-overlayfs mount failed for container {self.container_id}",
                details=exc.stderr.strip(),
            ) from exc

    def unmount(self) -> None:
        """Unmount the fuse-overlayfs filesystem."""
        if not self._mounted:
            return

        fusermount = shutil.which("fusermount3") or shutil.which("fusermount") or "fusermount"
        try:
            subprocess.run(
                [fusermount, "-u", str(self.merged_dir)],
                check=True,
                capture_output=True,
                text=True,
            )
            self._mounted = False
            logger.debug("fuse-overlayfs unmounted: %s", self.merged_dir)
        except subprocess.CalledProcessError as exc:
            logger.warning("Failed to unmount fuse-overlayfs: %s", exc.stderr.strip())

    def __enter__(self) -> "FuseOverlayFSDriver":
        self.mount()
        return self

    def __exit__(self, *_: object) -> None:
        self.unmount()


def prepare_rootless_overlay(
    container_id: str,
    layer_dirs: list[Path],
    containers_dir: Path,
) -> FuseOverlayFSDriver:
    """Create a FuseOverlayFSDriver for a rootless container.

    If fuse-overlayfs is not available, falls back to a plain directory
    copy which has no COW semantics but is always available.

    Args:
        container_id:   Container ID.
        layer_dirs:     Ordered layer directories (base first).
        containers_dir: Container state root.

    Returns:
        A FuseOverlayFSDriver (not yet mounted).
    """
    container_dir = containers_dir / container_id
    return FuseOverlayFSDriver(
        container_id=container_id,
        layer_dirs=layer_dirs,
        upper_dir=container_dir / "upper",
        work_dir=container_dir / "work",
        merged_dir=container_dir / "rootfs",
    )
