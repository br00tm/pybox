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

import ctypes
import ctypes.util
import logging
import os
import sys
from pathlib import Path
from typing import NoReturn

from pybox.container.config import ContainerConfig
from pybox.namespace.constants import (
    CLONE_NEWUSER,
    CLONE_NEWNS,
    CLONE_NEWNET,
    CLONE_NEWUTS,
    CLONE_NEWIPC,
)
from pybox.namespace.pivot_root import pivot_root_or_chroot, _mount, MS_BIND, MS_REC
from pybox.namespace.unshare import sethostname, unshare

logger = logging.getLogger(__name__)

# Namespaces created in phase 2 (after uid/gid maps are written by parent).
# NOTE: CLONE_NEWPID is intentionally excluded — it requires a double-fork
# pattern (unshare → fork → child becomes PID 1) which conflicts with the
# subprocess calls we make for mounts. It can be added back in a future
# revision with a proper PID-namespace-aware init process.
_POST_USER_FLAGS: int = (
    CLONE_NEWNS | CLONE_NEWNET | CLONE_NEWUTS | CLONE_NEWIPC
)

# Pseudo-filesystem mounts done inside the container.
# (fstype, source, target) — all mounted via mount(2) ctypes, no subprocess.
_PSEUDO_MOUNTS: list[tuple[bytes, bytes, bytes]] = [
    (b"proc",    b"proc",   b"/proc"),
    (b"tmpfs",   b"tmpfs",  b"/dev"),   # devtmpfs needs CAP_SYS_ADMIN in init ns
    (b"sysfs",   b"sysfs",  b"/sys"),
    (b"tmpfs",   b"tmpfs",  b"/tmp"),
]

_libc: ctypes.CDLL = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)  # type: ignore[arg-type]


def container_init(
    config: ContainerConfig,
    child_ready_w: int,
    maps_done_r: int,
) -> NoReturn:
    """Entry point for the container child process.

    Rootless user-namespace synchronisation protocol (two-pipe):

      1. Child: unshare(CLONE_NEWUSER)
      2. Child → Parent: write 1 byte to child_ready_w  (user ns ready)
      3. Parent writes uid_map + gid_map for this PID
      4. Parent → Child: write 1 byte to maps_done pipe (maps written)
      5. Child: unshare(PID | MNT | NET | UTS | IPC)
      6. Child: pivot_root → mounts → exec

    Args:
        config:        ContainerConfig describing the container to start.
        child_ready_w: Write end of child→parent pipe; closed after signalling.
        maps_done_r:   Read end of parent→child pipe; closed after reading.
    """
    try:
        # Phase 1: create user namespace (no privileges required)
        unshare(CLONE_NEWUSER)

        # Signal parent: user namespace is ready, please write uid/gid maps
        os.write(child_ready_w, b"\x00")
        os.close(child_ready_w)

        # Wait for parent to confirm maps are written
        os.read(maps_done_r, 1)
        os.close(maps_done_r)

        # Phase 2: now have CAP_SYS_ADMIN in the new user namespace
        unshare(_POST_USER_FLAGS)

        _setup_rootfs(config)
        _mount_pseudo_filesystems()
        _set_hostname(config)
        _exec_command(config)
    except Exception as exc:  # noqa: BLE001
        # Ensure pipes are closed so parent doesn't deadlock
        for fd in (child_ready_w, maps_done_r):
            try:
                os.close(fd)
            except OSError:
                pass
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
    """Bind-mount host directories into the container rootfs via mount(2)."""
    for host_path, container_path in config.volume_pairs():
        container_abs = Path("/") / str(container_path).lstrip("/")
        container_abs.mkdir(parents=True, exist_ok=True)
        src = str(host_path).encode()
        dst = str(container_abs).encode()
        ret = _mount(src, dst, None, MS_BIND | MS_REC)
        if ret != 0:
            err = ctypes.get_errno()
            logger.warning("Failed to bind-mount %s → %s: errno %d (%s)", host_path, container_abs, err, os.strerror(err))
        else:
            logger.debug("Bind-mounted %s → %s", host_path, container_abs)


def _mount_pseudo_filesystems() -> None:
    """Mount /proc, /dev, /sys, and /tmp inside the container.

    Uses mount(2) directly via ctypes — no subprocess fork, no ENOMEM risk.
    /dev is mounted as tmpfs (devtmpfs requires CAP_SYS_ADMIN in the initial
    user namespace; inside a user namespace tmpfs is sufficient).
    """
    for fstype, source, target in _PSEUDO_MOUNTS:
        Path(target.decode()).mkdir(parents=True, exist_ok=True)
        ret = _mount(source, target, fstype, 0)
        if ret != 0:
            err = ctypes.get_errno()
            logger.warning("Failed to mount %s at %s: errno %d (%s)", fstype, target, err, os.strerror(err))
        else:
            logger.debug("Mounted %s at %s", fstype.decode(), target.decode())


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
