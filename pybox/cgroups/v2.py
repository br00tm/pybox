"""cgroups v2 (unified hierarchy) interface for PyBox containers.

Creates and manages a cgroup hierarchy under:
    /sys/fs/cgroup/pybox/<container-id>/

Supports:
    - memory.max  — RAM limit
    - cpu.max     — CPU quota/period
    - pids.max    — maximum process count
    - cgroup.procs — add/remove PIDs

Reference: https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html
"""

from __future__ import annotations

import logging
from pathlib import Path

from pybox.cgroups.specs import CgroupSpec
from pybox.exceptions import CgroupError

logger = logging.getLogger(__name__)


class CgroupV2:
    """Manages a cgroups v2 hierarchy for a single container.

    Each container gets its own subdirectory under the pybox cgroup root:
        <cgroup_root>/pybox/<container_id>/

    Resource limits are applied by writing to the pseudo-files in that
    directory. The container's PIDs are added via cgroup.procs.
    """

    def __init__(self, container_id: str, cgroup_root: Path) -> None:
        """Initialise the cgroup manager for a container.

        Args:
            container_id: Short container ID (12 hex chars).
            cgroup_root:  Root of the PyBox cgroup hierarchy
                          (default: /sys/fs/cgroup/pybox).
        """
        self.container_id = container_id
        # /sys/fs/cgroup/pybox/<container_id>
        self.cgroup_path: Path = cgroup_root / container_id

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def create(self) -> None:
        """Create the cgroup directory for this container.

        The kernel automatically initialises the cgroup pseudo-files when
        the directory is created under the cgroup filesystem.

        Raises:
            CgroupError: If the directory cannot be created.
        """
        try:
            # mkdir triggers kernel cgroup creation — no special syscall needed
            self.cgroup_path.mkdir(parents=True, exist_ok=True)
            logger.debug("Created cgroup at %s", self.cgroup_path)
        except OSError as exc:
            raise CgroupError(
                f"Failed to create cgroup for container {self.container_id}",
                details=str(exc),
            ) from exc

    def delete(self) -> None:
        """Remove the cgroup directory after all PIDs have left.

        The kernel refuses to delete a cgroup that still has live processes.
        Call this only after the container has exited.

        Raises:
            CgroupError: If the directory cannot be removed.
        """
        try:
            # rmdir(2) on an empty cgroup directory destroys the cgroup
            self.cgroup_path.rmdir()
            logger.debug("Deleted cgroup at %s", self.cgroup_path)
        except FileNotFoundError:
            pass  # Already gone — nothing to do
        except OSError as exc:
            raise CgroupError(
                f"Failed to delete cgroup for container {self.container_id}",
                details=str(exc),
            ) from exc

    # ------------------------------------------------------------------
    # Resource limits
    # ------------------------------------------------------------------

    def apply_limits(self, spec: CgroupSpec) -> None:
        """Write resource limits from a CgroupSpec to the cgroup pseudo-files.

        Args:
            spec: Resource limit specification.

        Raises:
            CgroupError: If any write fails.
        """
        self._write("memory.max", spec.memory_max_value)
        self._write("cpu.max", spec.cpu_max_value)
        self._write("pids.max", spec.pids_max_value)

        logger.debug(
            "Applied cgroup limits for %s: memory=%s cpu=%s pids=%s",
            self.container_id,
            spec.memory_max_value,
            spec.cpu_max_value,
            spec.pids_max_value,
        )

    # ------------------------------------------------------------------
    # Process management
    # ------------------------------------------------------------------

    def add_pid(self, pid: int) -> None:
        """Add a process to this cgroup by writing its PID to cgroup.procs.

        Once the PID is written, the kernel moves the process (and all its
        threads) into this cgroup. All future children of the process will
        automatically inherit the cgroup.

        Args:
            pid: PID of the process to add.

        Raises:
            CgroupError: If the write fails.
        """
        # Writing a PID to cgroup.procs atomically assigns the process to
        # this cgroup. The kernel updates memory accounting immediately.
        self._write("cgroup.procs", str(pid))
        logger.debug("Added PID %d to cgroup %s", pid, self.container_id)

    def get_pids(self) -> list[int]:
        """Return the list of PIDs currently in this cgroup.

        Returns:
            List of integer PIDs, or empty list if the cgroup is gone.
        """
        procs_file = self.cgroup_path / "cgroup.procs"
        try:
            # cgroup.procs contains one PID per line
            content = procs_file.read_text()
            return [int(line) for line in content.splitlines() if line.strip()]
        except FileNotFoundError:
            return []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write(self, filename: str, value: str) -> None:
        """Write a value to a cgroup pseudo-file.

        Args:
            filename: Name of the cgroup file (relative to cgroup_path).
            value:    String value to write.

        Raises:
            CgroupError: If the write fails.
        """
        path = self.cgroup_path / filename
        try:
            # The kernel processes the write(2) immediately — no buffering
            path.write_text(value)
            logger.debug("cgroup write: %s = %r", path, value)
        except OSError as exc:
            raise CgroupError(
                f"Failed to write {filename!r} = {value!r} for container {self.container_id}",
                details=str(exc),
            ) from exc

    def _read(self, filename: str) -> str:
        """Read the current value of a cgroup pseudo-file.

        Args:
            filename: Name of the cgroup file.

        Returns:
            String content of the file, stripped of trailing whitespace.
        """
        path = self.cgroup_path / filename
        try:
            return path.read_text().strip()
        except FileNotFoundError:
            return ""
