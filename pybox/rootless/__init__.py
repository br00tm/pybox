"""Rootless container support: user namespaces, fuse-overlayfs, slirp4netns."""

from __future__ import annotations

import os
from pathlib import Path


def is_rootless() -> bool:
    """Return True if the current process is running without root privileges."""
    return os.getuid() != 0


def get_subuid_range(username: str) -> tuple[int, int]:
    """Parse /etc/subuid and return (start_uid, count) for the user.

    Args:
        username: Linux username to look up.

    Returns:
        Tuple of (subordinate_uid_start, count).

    Raises:
        KeyError: If the user has no entry in /etc/subuid.
    """
    return _parse_id_file(Path("/etc/subuid"), username)


def get_subgid_range(username: str) -> tuple[int, int]:
    """Parse /etc/subgid and return (start_gid, count) for the user.

    Args:
        username: Linux username to look up.

    Returns:
        Tuple of (subordinate_gid_start, count).

    Raises:
        KeyError: If the user has no entry in /etc/subgid.
    """
    return _parse_id_file(Path("/etc/subgid"), username)


def _parse_id_file(path: Path, username: str) -> tuple[int, int]:
    """Parse a /etc/subuid or /etc/subgid file.

    Format: <username>:<start_id>:<count>

    Args:
        path:     Path to the file.
        username: User to look up.

    Returns:
        (start_id, count) tuple.

    Raises:
        KeyError: If the user is not found.
    """
    if not path.exists():
        raise KeyError(f"{path} not found")

    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(":")
        if len(parts) != 3:
            continue
        if parts[0] == username:
            return int(parts[1]), int(parts[2])

    raise KeyError(f"No subid entry found for user '{username}' in {path}")
