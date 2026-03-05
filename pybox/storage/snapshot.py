"""OverlayFS upper-dir diff capture (snapshot → layer tarball).

After a build step runs inside an ephemeral container, the OverlayFS
upper/ directory contains only the files that were created or modified
during that step. This module captures those changes into a tar archive
suitable for use as an OCI layer.

Whiteout files are created for any paths that were deleted relative to
the lower layers (detected by comparing against the lower dir listing).

Reference: OCI Image Spec §5.4 Layer Changesets
"""

from __future__ import annotations

import io
import logging
import os
import tarfile
from pathlib import Path

from pybox.exceptions import StorageError

logger = logging.getLogger(__name__)

# OCI whiteout prefix
_WH = ".wh."
_WH_OPQ = ".wh..wh..opq"


class SnapshotManager:
    """Captures OverlayFS upper-dir diffs as OCI layer tarballs.

    Args:
        containers_dir: Root of container storage (e.g. <PYBOX_ROOT>/containers).
    """

    def __init__(self, containers_dir: Path) -> None:
        self._containers_dir = containers_dir

    def snapshot(self, container_id: str) -> Path:
        """Capture the upper dir diff of a container as a layer tar.

        Reads the OverlayFS upper/ directory and creates a tar file
        encoding all additions, modifications, and deletions relative
        to the lower layers.

        Args:
            container_id: Container whose upper dir to snapshot.

        Returns:
            Path to the created layer tarball (.tar.gz).

        Raises:
            StorageError: If the upper dir doesn't exist or tar creation fails.
        """
        container_dir = self._containers_dir / container_id
        upper_dir = container_dir / "upper"

        if not upper_dir.exists():
            raise StorageError(
                f"Upper dir for container {container_id} does not exist: {upper_dir}"
            )

        tar_path = container_dir / "layer.tar.gz"
        logger.debug("Snapshotting upper dir %s → %s", upper_dir, tar_path)

        try:
            with tarfile.open(tar_path, "w:gz") as tf:
                _add_upper_dir(tf, upper_dir)
        except (OSError, tarfile.TarError) as exc:
            raise StorageError(
                f"Failed to snapshot container {container_id}", details=str(exc)
            ) from exc

        size = tar_path.stat().st_size
        logger.debug("Snapshot created: %s (%d bytes)", tar_path, size)
        return tar_path


def _add_upper_dir(tf: tarfile.TarFile, upper_dir: Path) -> None:
    """Walk the upper dir and add all entries to the tar file.

    Handles OCI whiteout creation for opaque and regular deletions
    (kernel marks deleted files in upper/ with a special char device).

    Args:
        tf:        Open TarFile in write mode.
        upper_dir: OverlayFS upper directory to walk.
    """
    for root, dirs, files in os.walk(upper_dir):
        root_path = Path(root)
        rel_root = root_path.relative_to(upper_dir)

        for fname in files:
            file_path = root_path / fname
            rel_path = rel_root / fname

            stat = file_path.lstat()

            # OverlayFS encodes deleted files as character devices with
            # major=0, minor=0. We convert these to OCI whiteout files.
            if _is_whiteout_device(stat):
                wh_name = str(rel_root / f"{_WH}{fname}")
                _add_whiteout(tf, wh_name)
                continue

            tf.add(file_path, arcname=str(rel_path), recursive=False)

        # Check for opaque whiteout: trusted.overlay.opaque xattr
        dir_path = root_path
        if str(rel_root) != "." and _has_opaque_xattr(dir_path):
            opq_name = str(rel_root / _WH_OPQ)
            _add_whiteout(tf, opq_name)


def _is_whiteout_device(stat_result: os.stat_result) -> bool:
    """Return True if the stat result is an OverlayFS whiteout device."""
    import stat
    return (
        stat.S_ISCHR(stat_result.st_mode)
        and os.major(stat_result.st_rdev) == 0
        and os.minor(stat_result.st_rdev) == 0
    )


def _has_opaque_xattr(path: Path) -> bool:
    """Return True if the directory has the overlay opaque xattr set."""
    try:
        val = os.getxattr(str(path), "trusted.overlay.opaque")
        return val == b"y"
    except (OSError, AttributeError):
        return False


def _add_whiteout(tf: tarfile.TarFile, name: str) -> None:
    """Add an OCI whiteout file (empty regular file) to the tar."""
    info = tarfile.TarInfo(name=name)
    info.size = 0
    info.mode = 0o644
    tf.addfile(info, io.BytesIO(b""))
    logger.debug("Whiteout added: %s", name)
