"""User namespace setup for rootless containers.

Writes uid_map and gid_map for a process in a new user namespace,
using either direct write (for single-UID mappings) or the
newuidmap/newgidmap setuid binaries (for larger subuid ranges).

Reference:
    man 7 user_namespaces
    man 1 newuidmap
    man 1 newgidmap
"""

from __future__ import annotations

import logging
import os
import subprocess

from pybox.exceptions import NamespaceError
from pybox.namespace.user_map import IDMapping, write_gid_map, write_uid_map
from pybox.rootless import get_subgid_range, get_subuid_range

logger = logging.getLogger(__name__)


def _newuidmap_available() -> bool:
    """Return True if the newuidmap setuid binary is available."""
    import shutil
    return shutil.which("newuidmap") is not None


def setup_user_namespace(
    pid: int,
    *,
    uid: int | None = None,
    gid: int | None = None,
) -> None:
    """Write uid_map and gid_map for a rootless container process.

    Maps container UID 0 → the calling user's UID on the host.
    If newuidmap/newgidmap are available and subuid entries exist,
    uses them for a full subordinate UID range. Otherwise falls back
    to a single-UID direct write mapping.

    Args:
        pid: PID of the process in the new user namespace.
        uid: Override host UID (defaults to os.getuid()).
        gid: Override host GID (defaults to os.getgid()).

    Raises:
        NamespaceError: If writing the maps fails.
    """
    host_uid = uid if uid is not None else os.getuid()
    host_gid = gid if gid is not None else os.getgid()

    if _newuidmap_available():
        _setup_with_newuidmap(pid, host_uid, host_gid)
    else:
        _setup_direct(pid, host_uid, host_gid)


def _setup_direct(pid: int, host_uid: int, host_gid: int) -> None:
    """Write a single-UID mapping directly to /proc/<pid>/uid_map."""
    uid_mappings = [IDMapping(container_id=0, host_id=host_uid, count=1)]
    gid_mappings = [IDMapping(container_id=0, host_id=host_gid, count=1)]

    write_uid_map(pid, uid_mappings)
    write_gid_map(pid, gid_mappings)

    logger.debug("Direct UID map: 0 → %d (PID %d)", host_uid, pid)


def _setup_with_newuidmap(pid: int, host_uid: int, host_gid: int) -> None:
    """Use newuidmap/newgidmap binaries for subordinate ID mapping.

    Falls back to direct single-UID mapping if subuid/subgid entries
    are not configured for the current user.
    """
    import pwd
    try:
        username = pwd.getpwuid(host_uid).pw_name
        sub_uid_start, sub_uid_count = get_subuid_range(username)
        sub_gid_start, sub_gid_count = get_subgid_range(username)
    except (KeyError, Exception):
        logger.debug("No subuid range found; falling back to direct mapping")
        _setup_direct(pid, host_uid, host_gid)
        return

    # newuidmap <pid> <container-id> <host-id> <count> ...
    # Map: 0 → host_uid (1 UID) + 1 → sub_uid_start (sub_uid_count UIDs)
    uid_args = [str(pid), "0", str(host_uid), "1", "1", str(sub_uid_start), str(sub_uid_count)]
    gid_args = [str(pid), "0", str(host_gid), "1", "1", str(sub_gid_start), str(sub_gid_count)]

    try:
        subprocess.run(["newuidmap", *uid_args], check=True, capture_output=True)
        subprocess.run(["newgidmap", *gid_args], check=True, capture_output=True)
        logger.debug(
            "newuidmap: PID %d mapped UID 0→%d + 1→%d (%d)",
            pid, host_uid, sub_uid_start, sub_uid_count,
        )
    except subprocess.CalledProcessError as exc:
        raise NamespaceError(
            f"newuidmap/newgidmap failed for PID {pid}",
            details=exc.stderr.decode() if exc.stderr else str(exc),
        ) from exc
