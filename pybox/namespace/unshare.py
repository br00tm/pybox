"""unshare(2) syscall binding via ctypes.

Calls libc.so.6 directly — no subprocess, no shell. This is the function that
detaches the calling process from shared kernel namespaces, giving it its own
isolated view of PIDs, mounts, network interfaces, etc.

Usage:
    from pybox.namespace.unshare import unshare
    from pybox.namespace.constants import CLONE_NEWPID, CLONE_NEWNS
    unshare(CLONE_NEWPID | CLONE_NEWNS)
"""

from __future__ import annotations

import ctypes
import ctypes.util
import errno as errno_mod
import os

from pybox.exceptions import NamespaceError

# Load the C standard library. On Linux this is always libc.so.6.
_libc_name = ctypes.util.find_library("c")
if _libc_name is None:
    raise NamespaceError("libc not found — PyBox requires a Linux host")

_libc: ctypes.CDLL = ctypes.CDLL(_libc_name, use_errno=True)

# unshare(2) returns 0 on success, -1 on failure (errno set)
_libc.unshare.restype = ctypes.c_int
_libc.unshare.argtypes = [ctypes.c_int]

# sethostname(2) — sets the hostname of the current UTS namespace
_libc.sethostname.restype = ctypes.c_int
_libc.sethostname.argtypes = [ctypes.c_char_p, ctypes.c_size_t]


def unshare(flags: int) -> None:
    """Disassociate parts of the process execution context.

    Calls the unshare(2) Linux syscall with the given namespace flags. On
    success the calling process gets fresh copies of the named namespaces.
    On failure raises NamespaceError with the errno description.

    Args:
        flags: Bitwise OR of CLONE_NEW* constants (see namespace.constants).

    Raises:
        NamespaceError: If the syscall returns -1.
    """
    # unshare(2) — detach the calling process from shared namespaces.
    # The kernel atomically creates new namespace instances for every
    # CLONE_NEW* bit set in flags and attaches the process to them.
    ret: int = _libc.unshare(flags)
    if ret != 0:
        err = ctypes.get_errno()
        raise NamespaceError(
            f"unshare(flags={flags:#010x}) failed",
            details=f"[errno {err}] {os.strerror(err)}",
        )


def unshare_user() -> None:
    """Create a new user namespace for the calling process.

    Must be called before other CLONE_NEW* flags when running without root
    (rootless mode). The kernel allows unprivileged processes to create user
    namespaces, which then grant apparent UID 0 inside the namespace.
    """
    from pybox.namespace.constants import CLONE_NEWUSER

    # CLONE_NEWUSER — create a user namespace. Inside the new namespace the
    # process will appear to run as UID 0 (root) once uid_map is written.
    unshare(CLONE_NEWUSER)


def sethostname(name: str) -> None:
    """Set the hostname of the current UTS namespace via sethostname(2).

    Only meaningful after unsharing the UTS namespace (CLONE_NEWUTS).

    Args:
        name: New hostname string.

    Raises:
        NamespaceError: If the syscall fails.
    """
    encoded = name.encode()
    # sethostname(2) — updates the hostname in the current UTS namespace.
    ret: int = _libc.sethostname(encoded, len(encoded))
    if ret != 0:
        err = ctypes.get_errno()
        raise NamespaceError(
            f"sethostname('{name}') failed",
            details=f"[errno {err}] {os.strerror(err)}",
        )


def unshare_all_container_namespaces() -> None:
    """Unshare all namespaces used by a PyBox container in one syscall.

    Combines PID, mount, network, UTS, IPC, and user namespaces into a
    single unshare(2) call for efficiency. Note: CLONE_NEWPID takes effect
    for *child* processes — the calling process still has its original PID.
    """
    from pybox.namespace.constants import CONTAINER_CLONE_FLAGS

    unshare(CONTAINER_CLONE_FLAGS)
