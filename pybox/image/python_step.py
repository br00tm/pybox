"""Python environment builder for the [python] boxfile.toml section.

Installs a virtualenv and pip packages into the container rootfs, producing
a reproducible /opt/pybox-venv layer that can be cached between builds.

The runner uses the system Python inside the container (or downloads the
requested version from python.org if it differs from the system Python).
For Phase 2 we use the system Python inside the running container. A future
phase can add a proper CPython download mechanism.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from pybox.exceptions import ContainerError
from pybox.image.build_spec import PythonSpec

logger = logging.getLogger(__name__)

_VENV_PATH = "/opt/pybox-venv"
_PIP_FLAGS = [
    "--no-cache-dir",
    "--quiet",
    "--disable-pip-version-check",
]


class PythonStepRunner:
    """Installs a Python virtualenv + packages into a container rootfs.

    Designed to run *inside* the container's filesystem (after pivot_root),
    or via subprocess targeting a mounted rootfs.

    Args:
        rootfs: Path to the container's merged rootfs (host-side path).
    """

    def __init__(self, rootfs: Path) -> None:
        self._rootfs = rootfs

    def install_python_env(self, spec: PythonSpec) -> None:
        """Create a venv at /opt/pybox-venv and pip install packages.

        Executed via `chroot <rootfs> <cmd>` from the host, so the
        container's own Python interpreter and libraries are used.

        Args:
            spec: PythonSpec from the [python] boxfile section.

        Raises:
            ContainerError: If venv creation or pip install fails.
        """
        venv_abs = self._rootfs / spec.venv_path.lstrip("/")

        if not venv_abs.exists():
            self._run_in_rootfs(
                [f"python{spec.version}", "-m", "venv", spec.venv_path],
                description="creating venv",
            )

        if spec.packages:
            pip_cmd = [
                f"{spec.venv_path}/bin/pip",
                "install",
                *_PIP_FLAGS,
                *spec.packages,
            ]
            self._run_in_rootfs(pip_cmd, description="installing packages")

        # Freeze installed packages for reproducibility
        freeze_path = venv_abs / "requirements-frozen.txt"
        result = self._run_in_rootfs(
            [f"{spec.venv_path}/bin/pip", "freeze"],
            description="freezing requirements",
            capture=True,
        )
        if result:
            freeze_path.write_text(result)

        logger.info(
            "Python env installed at %s (%d packages)",
            spec.venv_path,
            len(spec.packages),
        )

    def _run_in_rootfs(
        self, cmd: list[str], *, description: str = "", capture: bool = False
    ) -> str | None:
        """Run a command inside the rootfs via chroot.

        Args:
            cmd:         Command and args to run inside the rootfs.
            description: Human-readable description for error messages.
            capture:     If True, capture and return stdout.

        Returns:
            stdout string if capture=True, else None.

        Raises:
            ContainerError: If the command exits non-zero.
        """
        full_cmd = ["chroot", str(self._rootfs), *cmd]
        logger.debug("Python step: %s", " ".join(cmd))

        try:
            result = subprocess.run(
                full_cmd,
                check=True,
                capture_output=capture,
                text=True,
            )
            return result.stdout if capture else None
        except subprocess.CalledProcessError as exc:
            raise ContainerError(
                f"Python step failed ({description}): {' '.join(cmd)}",
                details=exc.stderr.strip() if exc.stderr else str(exc),
            ) from exc
        except FileNotFoundError as exc:
            raise ContainerError(
                "chroot not found — must run as root for Python step",
                details=str(exc),
            ) from exc
