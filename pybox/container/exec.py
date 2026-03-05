"""Execute a command in a running container via setns(2).

Implements `pybox exec <id> -- <cmd>` by:
    1. Looking up the container's PID from state.
    2. Opening /proc/<pid>/ns/* file descriptors.
    3. Calling setns(2) for each namespace to enter the container's context.
    4. Forking and execvp()-ing the user command.

setns(2) is called via ctypes (syscall number 308 on x86-64).
TTY support uses os.openpty() for interactive sessions.

Reference: man 2 setns, man 1 nsenter
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import platform
from pathlib import Path

from pybox.exceptions import ContainerError, ContainerNotFoundError, NamespaceError

logger = logging.getLogger(__name__)

# setns(2) syscall numbers by architecture
_SETNS_SYSCALLS: dict[str, int] = {
    "x86_64": 308,
    "aarch64": 268,
    "armv7l": 375,
}

_libc_name = ctypes.util.find_library("c")
_libc: ctypes.CDLL | None = None
if _libc_name:
    _libc = ctypes.CDLL(_libc_name, use_errno=True)
    _libc.syscall.restype = ctypes.c_long
    _libc.syscall.argtypes = [ctypes.c_long, ctypes.c_int, ctypes.c_int]

# Namespace types to enter (in order).
# user MUST come first: entering it grants the capabilities (CAP_SYS_ADMIN)
# required to then enter ipc/uts/net/pid/mnt without EPERM.
_NS_TYPES = ["user", "ipc", "uts", "net", "pid", "mnt"]


def _setns(fd: int, nstype: int = 0) -> None:
    """Call setns(2) to enter the namespace referred to by fd.

    Args:
        fd:     Open file descriptor for a /proc/<pid>/ns/* file.
        nstype: Namespace type (0 = detect automatically).

    Raises:
        NamespaceError: If setns fails.
    """
    if _libc is None:
        raise NamespaceError("libc not found")

    arch = platform.machine()
    nr = _SETNS_SYSCALLS.get(arch)
    if nr is None:
        raise NamespaceError(f"setns syscall number unknown for arch {arch}")

    # setns(2) — attach calling thread to namespace referred to by fd
    ret: int = _libc.syscall(nr, fd, nstype)
    if ret != 0:
        import ctypes
        err = ctypes.get_errno()
        raise NamespaceError(
            f"setns(fd={fd}) failed",
            details=f"[errno {err}] {os.strerror(err)}",
        )


def exec_in_container(
    container_id: str,
    cmd: list[str],
    containers_dir: Path,
    *,
    tty: bool = False,
) -> int:
    """Execute a command inside a running container.

    Opens the container's namespace file descriptors, enters each namespace
    via setns(2), then forks and executes the command.

    Args:
        container_id:   Container ID.
        cmd:            Command and arguments to execute.
        containers_dir: Root of container state storage.
        tty:            If True, allocate a pseudo-terminal.

    Returns:
        Exit code of the executed command.

    Raises:
        ContainerNotFoundError: If the container doesn't exist.
        ContainerError:         If the container is not running.
        NamespaceError:         If setns fails.
    """
    import json

    state_file = containers_dir / container_id / "state.json"
    if not state_file.exists():
        raise ContainerNotFoundError(container_id)

    state = json.loads(state_file.read_text())
    pid: int | None = state.get("pid")
    if not pid or state.get("state") != "running":
        raise ContainerError(f"Container {container_id} is not running")

    # Open namespace file descriptors before entering any namespace
    ns_fds: dict[str, int] = {}
    try:
        for ns in _NS_TYPES:
            ns_path = f"/proc/{pid}/ns/{ns}"
            try:
                ns_fds[ns] = os.open(ns_path, os.O_RDONLY)
            except OSError:
                pass  # Some namespaces may not be available

        # Allocate PTY if requested
        master_fd: int | None = None
        slave_fd: int | None = None
        if tty:
            master_fd, slave_fd = os.openpty()

        child_pid = os.fork()
        if child_pid == 0:
            # ---- Child: enter namespaces then exec ----
            try:
                _enter_namespaces(ns_fds)

                if tty and slave_fd is not None:
                    # Redirect stdio to the slave PTY
                    os.dup2(slave_fd, 0)
                    os.dup2(slave_fd, 1)
                    os.dup2(slave_fd, 2)
                    if slave_fd > 2:
                        os.close(slave_fd)

                env = {
                    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                    "HOME": "/root",
                    "TERM": os.environ.get("TERM", "xterm"),
                }
                os.execvpe(cmd[0], cmd, env)
            except Exception as exc:
                import sys
                print(f"exec error: {exc}", file=sys.stderr)
                os._exit(1)

        # ---- Parent ----
        for fd in ns_fds.values():
            os.close(fd)

        if tty and master_fd is not None and slave_fd is not None:
            os.close(slave_fd)
            _forward_tty(master_fd)
            os.close(master_fd)

        _, wait_status = os.waitpid(child_pid, 0)
        return os.waitstatus_to_exitcode(wait_status)

    except Exception:
        for fd in ns_fds.values():
            try:
                os.close(fd)
            except OSError:
                pass
        raise


def _enter_namespaces(ns_fds: dict[str, int]) -> None:
    """Enter each namespace by calling setns for each open fd."""
    for ns_name, fd in ns_fds.items():
        try:
            _setns(fd, 0)
            logger.debug("Entered %s namespace", ns_name)
        except NamespaceError as exc:
            logger.warning("Failed to enter %s namespace: %s", ns_name, exc)
        finally:
            os.close(fd)


def _forward_tty(master_fd: int) -> None:
    """Forward data between the master PTY and the current terminal."""
    import select
    import sys
    import termios
    import tty

    try:
        old_settings = termios.tcgetattr(sys.stdin.fileno())
        tty.setraw(sys.stdin.fileno())
    except Exception:
        old_settings = None

    try:
        while True:
            rlist, _, _ = select.select([master_fd, sys.stdin.fileno()], [], [], 0.1)
            if master_fd in rlist:
                try:
                    data = os.read(master_fd, 4096)
                    if not data:
                        break
                    os.write(sys.stdout.fileno(), data)
                except OSError:
                    break
            if sys.stdin.fileno() in rlist:
                try:
                    data = os.read(sys.stdin.fileno(), 4096)
                    if not data:
                        break
                    os.write(master_fd, data)
                except OSError:
                    break
    finally:
        if old_settings is not None:
            try:
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_settings)
            except Exception:
                pass
