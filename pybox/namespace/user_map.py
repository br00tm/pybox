"""UID/GID mapping for rootless container support.

In a user namespace the kernel maps host UIDs/GIDs to container UIDs/GIDs.
Writing to /proc/<pid>/uid_map and /proc/<pid>/gid_map establishes this
mapping. The container process then appears to be UID 0 (root) inside the
namespace while remaining an unprivileged user on the host.

Format of uid_map / gid_map (one mapping per line):
    <container-id>  <host-id>  <count>

Example: "0 1000 1" maps container UID 0 → host UID 1000, count 1.

Reference: man 7 user_namespaces, /proc/PID/uid_map
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from pybox.exceptions import NamespaceError


@dataclass(frozen=True)
class IDMapping:
    """A single UID or GID mapping entry."""

    # First ID in the container namespace
    container_id: int
    # Corresponding first ID on the host
    host_id: int
    # Number of consecutive IDs to map
    count: int

    def __str__(self) -> str:
        # Format required by the kernel: "<container_id> <host_id> <count>\n"
        return f"{self.container_id} {self.host_id} {self.count}"


def _write_id_map(path: str, mappings: list[IDMapping]) -> None:
    """Write a uid_map or gid_map file for the given process.

    The kernel requires the file to be written in a single write(2) call,
    so we open with 'w' (which uses a single write internally via Python's
    buffered I/O).

    Args:
        path:     Absolute path, e.g. /proc/12345/uid_map
        mappings: List of ID mappings to write.

    Raises:
        NamespaceError: If the write fails (e.g., process already mapped).
    """
    content = "\n".join(str(m) for m in mappings) + "\n"
    try:
        # The kernel enforces that uid_map/gid_map are written atomically.
        # Using open() + write() satisfies this as Python buffers the write.
        with open(path, "w") as f:
            f.write(content)
    except OSError as exc:
        raise NamespaceError(
            f"Failed to write ID map to {path}",
            details=str(exc),
        ) from exc


def _disable_setgroups(pid: int) -> None:
    """Write 'deny' to /proc/<pid>/setgroups before writing gid_map.

    Since Linux 3.19, unprivileged processes must write "deny" to
    /proc/<pid>/setgroups before they can write gid_map. This prevents
    privilege escalation via the setgroups(2) syscall.

    Args:
        pid: Target process PID.
    """
    path = f"/proc/{pid}/setgroups"
    try:
        with open(path, "w") as f:
            # "deny" prevents the process from calling setgroups(2),
            # which is required before writing gid_map as non-root.
            f.write("deny")
    except OSError as exc:
        raise NamespaceError(
            f"Failed to write setgroups for pid {pid}",
            details=str(exc),
        ) from exc


def write_uid_map(pid: int, mappings: list[IDMapping]) -> None:
    """Write UID mappings for the process with the given PID.

    Args:
        pid:      Target process PID (typically the container's PID 1).
        mappings: UID mapping entries.
    """
    _write_id_map(f"/proc/{pid}/uid_map", mappings)


def write_gid_map(pid: int, mappings: list[IDMapping]) -> None:
    """Write GID mappings for the process with the given PID.

    Must call _disable_setgroups() first when running without CAP_SETGID.

    Args:
        pid:      Target process PID.
        mappings: GID mapping entries.
    """
    # Deny setgroups(2) to satisfy kernel security requirement before
    # writing gid_map from an unprivileged process.
    _disable_setgroups(pid)
    _write_id_map(f"/proc/{pid}/gid_map", mappings)


def setup_rootless_id_maps(
    pid: int,
    *,
    host_uid: int | None = None,
    host_gid: int | None = None,
) -> None:
    """Configure UID/GID maps for a rootless container process.

    Maps container UID 0 → the current host UID (or host_uid if provided),
    and container GID 0 → the current host GID (or host_gid if provided).
    This makes the process appear as root inside the container.

    Args:
        pid:      PID of the process in the new user namespace.
        host_uid: Host UID to map to container UID 0. Defaults to os.getuid().
        host_gid: Host GID to map to container GID 0. Defaults to os.getgid().
    """
    if host_uid is None:
        # Use the calling process's real UID as the host side of the mapping
        host_uid = os.getuid()
    if host_gid is None:
        # Use the calling process's real GID as the host side of the mapping
        host_gid = os.getgid()

    # Map container root (UID 0) → host UID; count=1 maps exactly one UID
    uid_mappings = [IDMapping(container_id=0, host_id=host_uid, count=1)]

    # Map container root (GID 0) → host GID; count=1 maps exactly one GID
    gid_mappings = [IDMapping(container_id=0, host_id=host_gid, count=1)]

    write_uid_map(pid, uid_mappings)
    write_gid_map(pid, gid_mappings)
