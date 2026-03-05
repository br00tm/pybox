"""pivot_root(2) syscall binding via ctypes.

pivot_root changes the root filesystem of the calling process's mount
namespace. The old root is moved to put_old, and new_root becomes /.

This is the preferred technique over chroot(2) because:
  1. pivot_root completely changes the mount namespace root.
  2. chroot only changes the process's perception of / but the old root is
     still accessible via ".." past the chroot boundary.

Falls back to chroot if pivot_root fails (e.g., in environments without
a proper mount namespace or when running in certain container runtimes).

Reference: man 2 pivot_root, Linux kernel source fs/namespace.c
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import subprocess

from pybox.exceptions import NamespaceError, StorageError
from pybox.namespace.constants import SYS_PIVOT_ROOT

# Load libc for the raw syscall(2) trampoline.
_libc_name = ctypes.util.find_library("c")
if _libc_name is None:
    raise NamespaceError("libc not found")

_libc: ctypes.CDLL = ctypes.CDLL(_libc_name, use_errno=True)

# syscall(2) — generic syscall trampoline. Used because Python's ctypes
# doesn't expose pivot_root directly (it's not in the stable C library ABI).
_libc.syscall.restype = ctypes.c_long
_libc.syscall.argtypes = [ctypes.c_long, ctypes.c_char_p, ctypes.c_char_p]


def pivot_root(new_root: str, put_old: str) -> None:
    """Change the root filesystem via the pivot_root(2) syscall.

    After this call:
      - new_root becomes the new /
      - the old root filesystem is available at put_old (relative to new_root)

    The caller must ensure:
      - new_root is a mount point (bind-mount it to itself if needed)
      - put_old is inside new_root
      - The calling process is in a new mount namespace (CLONE_NEWNS)

    Args:
        new_root: Path to the new root filesystem directory.
        put_old:  Path (inside new_root) where the old root will be placed.

    Raises:
        NamespaceError: If the pivot_root syscall fails and chroot also fails.
    """
    new_root_b = new_root.encode()
    put_old_b = put_old.encode()

    # syscall(SYS_PIVOT_ROOT, new_root, put_old) — ask the kernel to pivot.
    # SYS_PIVOT_ROOT = 155 on x86-64 (stable since Linux 2.4).
    ret: int = _libc.syscall(SYS_PIVOT_ROOT, new_root_b, put_old_b)
    if ret != 0:
        err = ctypes.get_errno()
        raise NamespaceError(
            f"pivot_root('{new_root}', '{put_old}') failed",
            details=f"[errno {err}] {os.strerror(err)}",
        )


def setup_rootfs(new_root: str) -> None:
    """Bind-mount new_root over itself so it qualifies as a mount point.

    pivot_root(2) requires new_root to be a mount point. If new_root was
    simply a directory, we make it one by bind-mounting it onto itself.

    Uses subprocess for the mount command (the only approved exception in
    PyBox for mount operations, per ARCHITECTURE.md).

    Args:
        new_root: Path to the new root filesystem directory.

    Raises:
        StorageError: If the bind mount fails.
    """
    try:
        # MS_BIND (0x1000) | MS_REC (0x4000) — recursively bind-mount new_root
        # onto itself so the kernel considers it a proper mount point.
        subprocess.run(
            ["mount", "--bind", new_root, new_root],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise StorageError(
            f"Failed to bind-mount {new_root} onto itself",
            details=exc.stderr.strip(),
        ) from exc


def pivot_root_or_chroot(new_root: str, put_old_name: str = ".old_root") -> None:
    """Attempt pivot_root; fall back to chroot if it fails.

    This is the recommended entry point for switching to the container
    filesystem. It handles:
      1. Making new_root a proper mount point (bind-mount)
      2. Creating put_old directory inside new_root
      3. Calling pivot_root(2)
      4. Changing to the new / and unmounting put_old
      5. Falling back to chroot(2) if step 3 fails

    Args:
        new_root:     Absolute path to the container's root filesystem.
        put_old_name: Directory name (inside new_root) for the old root.
    """

    put_old_abs = os.path.join(new_root, put_old_name.lstrip("/"))
    os.makedirs(put_old_abs, exist_ok=True)

    # Ensure new_root is a mount point before calling pivot_root
    setup_rootfs(new_root)

    try:
        # pivot_root moves the current root to put_old and makes new_root the
        # new root of the mount namespace.
        pivot_root(new_root, put_old_abs)

        # Change working directory to the new root so relative paths work.
        os.chdir("/")

        # Unmount the old root — we no longer need access to the host FS.
        # MNT_DETACH (lazy unmount) ensures the unmount succeeds even if
        # something still has files open under put_old.
        _lazy_umount(f"/{put_old_name}")

        # Remove the now-empty put_old directory
        try:
            os.rmdir(f"/{put_old_name}")
        except OSError:
            pass  # Non-fatal — directory may not be empty yet

    except NamespaceError:
        # Fallback: use chroot(2). Less secure but works in environments
        # without a proper mount namespace (e.g., some CI systems).
        os.chroot(new_root)
        os.chdir("/")


def _lazy_umount(path: str) -> None:
    """Unmount path with MNT_DETACH (lazy unmount) via subprocess.

    Args:
        path: The mount point to unmount.
    """
    try:
        subprocess.run(
            ["umount", "-l", path],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError:
        pass  # Non-fatal; the container still works without the cleanup
