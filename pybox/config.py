"""Global configuration via Pydantic BaseSettings.

All settings can be overridden via environment variables prefixed with PYBOX_.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings


class PyBoxConfig(BaseSettings):
    """Global PyBox runtime configuration."""

    model_config = {"env_prefix": "PYBOX_", "env_file": ".env", "env_file_encoding": "utf-8"}

    # Storage root for images, containers, volumes
    root: Path = Field(default=Path("/var/lib/pybox"), alias="PYBOX_ROOT")

    # cgroups v2 hierarchy root
    cgroup_root: Path = Field(
        default=Path("/sys/fs/cgroup/pybox"), alias="PYBOX_CGROUP_ROOT"
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
