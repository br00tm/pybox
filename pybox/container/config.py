"""Pydantic model for container runtime configuration.

ContainerConfig holds every parameter needed to create and start a container:
    - the image reference
    - the command to execute
    - environment variables
    - volume mounts
    - resource limits
    - network mode
    - resolved rootfs path (set after image pull and OverlayFS setup)
"""

from __future__ import annotations

import uuid
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from pybox.cgroups.specs import CgroupSpec


def _generate_id() -> str:
    """Generate a short (12-char hex) container ID."""
    # uuid4().hex produces a 32-char hex string; we take the first 12
    return uuid.uuid4().hex[:12]


class ContainerConfig(BaseModel):
    """Full configuration for a PyBox container.

    Fields:
        id:           12-hex-char container identifier (auto-generated).
        image:        Image reference used to create this container.
        cmd:          Command and arguments to execute inside the container.
        env:          Environment variables as a dict (KEY → VALUE).
        volumes:      Bind-mount specifications in "src:dst" or "src:dst:opts" format.
        hostname:     Hostname to set in the container's UTS namespace.
                      Defaults to the container ID if not set.
        cgroup_spec:  Resource limits (memory, cpu, pids). None means unlimited.
        rootfs:       Absolute path to the container's merged OverlayFS root.
                      Set by ContainerManager after storage is prepared.
        network_mode: Networking mode — "bridge" (default), "host", or "none".
        name:         Optional human-readable name for the container.
    """

    id: str = Field(default_factory=_generate_id)
    image: str
    cmd: list[str]
    env: dict[str, str] = Field(default_factory=dict)
    volumes: list[str] = Field(default_factory=list)
    hostname: str | None = None
    cgroup_spec: CgroupSpec | None = None
    rootfs: Path | None = None
    network_mode: str = "bridge"
    name: str | None = None

    model_config = {"arbitrary_types_allowed": True}

    @model_validator(mode="after")
    def set_default_hostname(self) -> "ContainerConfig":
        """Use the container ID as hostname if none was provided."""
        if self.hostname is None:
            # A short hostname derived from the container ID is the Docker default
            object.__setattr__(self, "hostname", self.id)
        return self

    def env_list(self) -> list[str]:
        """Return env vars as a list of 'KEY=VALUE' strings for os.execvpe."""
        return [f"{k}={v}" for k, v in self.env.items()]

    def volume_pairs(self) -> list[tuple[Path, Path]]:
        """Parse volume specs into (host_path, container_path) tuples.

        Ignores the optional third `:opts` field (e.g. `:ro`).
        """
        pairs: list[tuple[Path, Path]] = []
        for spec in self.volumes:
            parts = spec.split(":")
            if len(parts) >= 2:
                pairs.append((Path(parts[0]), Path(parts[1])))
        return pairs
