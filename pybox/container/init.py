"""Container init process (PID 1 inside the new namespaces).

This module is called from the child process immediately after fork().
It runs entirely inside the freshly-created namespaces and must never
return — it ends with os.execvpe() replacing itself with the user command.

Execution order:
    1. Unshare all container namespaces (PID, MNT, NET, UTS, IPC, USER)
    2. Write uid_map / gid_map to become root inside the user namespace
    3. Bind-mount new_root onto itself to satisfy pivot_root requirement
    4. Call pivot_root to switch filesystem root to the container rootfs
    5. Mount essential pseudo-filesystems: /proc, /dev, /sys, /tmp
    6. Set the container hostname from ContainerConfig
    7. Apply environment variables
    8. os.execvpe() — hand off to the user command (never returns)

If any step fails, the child exits with a non-zero code which is detected
by the parent's os.waitpid() call.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

from pybox.container.config import ContainerConfig
from pybox.namespace.constants import (
    CLONE_NEWUSER,
    CLONE_NEWPID,
    CLONE_NEWNS,
    CLONE_NEWNET,
    CLONE_NEWUTS,
    CLONE_NEWIPC,
)
from pybox.namespace.pivot_root import pivot_root_or_chroot
from pybox.namespace.unshare import sethostname, unshare

logger = logging.getLogger(__name__)

# Namespaces that require the user namespace to already have uid/gid maps
# written before they can be created by an unprivileged process.
_POST_USER_FLAGS: int = (
    CLONE_NEWPID | CLONE_NEWNS | CLONE_NEWNET | CLONE_NEWUTS | CLONE_NEWIPC
)

# Pseudo-filesystem mount specs: (fstype, source, target)
_PSEUDO_MOUNTS: list[tuple[str, str, str]] = [
    ("proc", "proc", "/proc"),
    ("devtmpfs", "dev", "/dev"),
    ("sysfs", "sysfs", "/sys"),
    ("tmpfs", "tmpfs", "/tmp"),
]


def container_init(config: ContainerConfig, sync_read_fd: int) -> NoReturn:
    """Entry point for the container child process.

    Two-phase namespace setup required for rootless containers:
      Phase 1 — unshare CLONE_NEWUSER only
      Sync    — read one byte from sync_read_fd (parent writes uid/gid maps then signals)
      Phase 2 — unshare PID, MNT, NET, UTS, IPC (now have capabilities via user ns)

    Args:
        config:       ContainerConfig describing the container to start.
        sync_read_fd: Read end of a pipe; parent closes it after writing uid/gid maps.
    """
    try:
        # Phase 1: create user namespace (works without any privileges)
        unshare(CLONE_NEWUSER)

        # Sync: wait for parent to write uid_map and gid_map, then signal us
        try:
            os.read(sync_read_fd, 1)
        finally:
            os.close(sync_read_fd)

        # Phase 2: now that we have uid 0 in the user namespace, create the rest
        unshare(_POST_USER_FLAGS)

        _setup_rootfs(config)
        _mount_pseudo_filesystems()
        _set_hostname(config)
        _exec_command(config)
    except Exception as exc:  # noqa: BLE001
        print(f"[pybox init] FATAL: {exc}", file=sys.stderr, flush=True)
        os._exit(1)  # noqa: SLF001


def _setup_rootfs(config: ContainerConfig) -> None:
    """Switch the filesystem root to the container's OverlayFS rootfs.

    Requires config.rootfs to be set (done by ContainerManager before fork).
    """
    if config.rootfs is None:
        raise RuntimeError("ContainerConfig.rootfs must be set before calling container_init")

    rootfs = str(config.rootfs)
    logger.debug("Pivoting root to %s", rootfs)

    # pivot_root_or_chroot handles:
    #   1. Bind-mounting rootfs onto itself (required by pivot_root)
    #   2. Creating .old_root inside rootfs
    #   3. Calling pivot_root(2) or falling back to chroot(2)
    #   4. Lazy-unmounting and removing .old_root
    pivot_root_or_chroot(rootfs)

    # Apply any volume bind mounts declared in the config
    _bind_volumes(config)


def _bind_volumes(config: ContainerConfig) -> None:
    """Bind-mount host directories into the container rootfs.

    Volume specs have already been parsed by setup_rootfs after pivot_root,
    so paths here are relative to the new root (/).
    """
    for host_path, container_path in config.volume_pairs():
        container_abs = Path("/") / str(container_path).lstrip("/")
        container_abs.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(
                ["mount", "--bind", str(host_path), str(container_abs)],
                check=True,
                capture_output=True,
                text=True,
            )
            logger.debug("Bind-mounted %s → %s", host_path, container_abs)
        except subprocess.CalledProcessError as exc:
            logger.warning("Failed to bind-mount %s: %s", host_path, exc.stderr.strip())


def _mount_pseudo_filesystems() -> None:
    """Mount /proc, /dev, /sys, and /tmp inside the container.

    These are virtual filesystems provided by the kernel and are not
    inherited from the host after pivot_root + mount namespace isolation.
    """
    for fstype, source, target in _PSEUDO_MOUNTS:
        target_path = Path(target)
        target_path.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(
                ["mount", "-t", fstype, source, target],
                check=True,
                capture_output=True,
                text=True,
            )
            logger.debug("Mounted %s at %s", fstype, target)
        except subprocess.CalledProcessError as exc:
            # /dev may fail in some environments (e.g. devtmpfs not available)
            # Log and continue — /proc is the most critical
            logger.warning("Failed to mount %s at %s: %s", fstype, target, exc.stderr.strip())


def _set_hostname(config: ContainerConfig) -> None:
    """Set the container's hostname in the new UTS namespace."""
    hostname = config.hostname or config.id
    sethostname(hostname)
    logger.debug("Hostname set to '%s'", hostname)


def _exec_command(config: ContainerConfig) -> NoReturn:
    """Replace this process with the container's user command.

    Uses os.execvpe so that the user command becomes PID 1 (or the first
    child of PID 1 in the new PID namespace, depending on fork strategy).

    Args:
        config: ContainerConfig with cmd and env.
    """
    cmd = config.cmd
    if not cmd:
        raise RuntimeError("ContainerConfig.cmd must not be empty")

    # Build the environment: start from a minimal base, apply config env
    env: dict[str, str] = {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": "/root",
        "TERM": os.environ.get("TERM", "xterm"),
    }
    env.update(config.env)

    logger.debug("exec: %s", cmd)

    # os.execvpe — replaces the current process image. Never returns on success.
    os.execvpe(cmd[0], cmd, env)
    # Unreachable — execvpe either succeeds (no return) or raises OSError
    raise RuntimeError(f"os.execvpe failed for command: {cmd}")
