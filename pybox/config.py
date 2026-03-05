"""Global configuration via Pydantic BaseSettings.

All settings can be overridden via environment variables prefixed with PYBOX_.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings


def _default_root() -> Path:
    """Return /var/lib/pybox when running as root, ~/.local/share/pybox otherwise."""
    if os.getuid() == 0:
        return Path("/var/lib/pybox")
    return Path.home() / ".local" / "share" / "pybox"


def _default_cgroup_root() -> Path:
    """Return the best available cgroup v2 root for the current user.

    Priority:
        1. /sys/fs/cgroup/pybox          — root (always writable)
        2. user@<uid>.service cgroup     — systemd delegated user slice (rootless)
        3. /sys/fs/cgroup/pybox          — fallback (will fail at runtime if not root,
                                           but CgroupV2 errors are caught and skipped)
    """
    if os.getuid() == 0:
        return Path("/sys/fs/cgroup/pybox")
    uid = os.getuid()
    # systemd delegates this slice to unprivileged users on modern distros
    delegated = Path(f"/sys/fs/cgroup/user.slice/user-{uid}.slice/user@{uid}.service")
    if delegated.exists() and os.access(str(delegated), os.W_OK):
        return delegated / "pybox"
    return Path("/sys/fs/cgroup/pybox")


class PyBoxConfig(BaseSettings):
    """Global PyBox runtime configuration."""

    model_config = {"env_prefix": "PYBOX_", "env_file": ".env", "env_file_encoding": "utf-8"}

    # Storage root for images, containers, volumes
    root: Path = Field(default_factory=_default_root, alias="PYBOX_ROOT")

    # cgroups v2 hierarchy root
    cgroup_root: Path = Field(
        default_factory=_default_cgroup_root, alias="PYBOX_CGROUP_ROOT"
    )

    # Logging level: DEBUG, INFO, WARNING, ERROR
    log_level: str = Field(default="INFO", alias="PYBOX_LOG_LEVEL")

    # Optional registry mirror URL (e.g. a pull-through cache)
    registry_mirror: str | None = Field(default=None, alias="PYBOX_REGISTRY_MIRROR")

    # Docker Hub registry base URL
    registry_url: str = Field(
        default="https://registry-1.docker.io", alias="PYBOX_REGISTRY_URL"
    )

    # Docker Hub auth service URL
    auth_url: str = Field(
        default="https://auth.docker.io", alias="PYBOX_AUTH_URL"
    )

    @property
    def images_dir(self) -> Path:
        """Directory where image layers are stored."""
        return self.root / "images"

    @property
    def containers_dir(self) -> Path:
        """Directory where container state is stored."""
        return self.root / "containers"

    @property
    def volumes_dir(self) -> Path:
        """Directory where named volumes are stored."""
        return self.root / "volumes"


# Module-level default; callers should prefer passing config explicitly.
_default_config: PyBoxConfig | None = None


def get_config() -> PyBoxConfig:
    """Return the process-wide default config (lazy-initialised)."""
    global _default_config
    if _default_config is None:
        _default_config = PyBoxConfig()
    return _default_config
