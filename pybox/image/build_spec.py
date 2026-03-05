"""Pydantic models for the PyBox image build specification (boxfile.toml).

The build spec is the parsed, validated in-memory representation of
a boxfile.toml file. It drives the ImageBuilder step-by-step.

Discriminated union for build steps allows clean parsing:
    [[steps]]
    name = "install deps"
    run  = "apt-get install -y curl"          # → RunStep

    [[steps]]
    name = "copy source"
    copy = { src = "./src", dst = "/app" }    # → CopyStep

    [[steps]]
    name = "set workdir"
    workdir = "/app"                           # → WorkdirStep

    [[steps]]
    name = "set env"
    env = { FOO = "bar" }                      # → EnvStep
"""

from __future__ import annotations

import hashlib
import json
from typing import Union

from pydantic import BaseModel, Field


# ------------------------------------------------------------------
# Step types (discriminated union on presence of fields)
# ------------------------------------------------------------------

class RunStep(BaseModel):
    """A shell command to execute during the build."""

    name: str = ""
    run: str  # Shell command string, e.g. "apt-get install -y curl"

    def cache_key(self, parent_digest: str = "") -> str:
        """Compute a deterministic sha256 cache key for this step.

        The key changes if the command or the parent layer digest changes,
        ensuring correct cache invalidation.
        """
        payload = json.dumps({"type": "run", "run": self.run, "parent": parent_digest}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()


class CopySpec(BaseModel):
    """Source/destination for a COPY step."""
    src: str
    dst: str


class CopyStep(BaseModel):
    """Copy files from the build context into the image."""

    name: str = ""
    copy: CopySpec

    def cache_key(self, parent_digest: str = "") -> str:
        payload = json.dumps(
            {"type": "copy", "src": self.copy.src, "dst": self.copy.dst, "parent": parent_digest},
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()


class WorkdirStep(BaseModel):
    """Set the working directory for subsequent steps and the container runtime."""

    name: str = ""
    workdir: str

    def cache_key(self, parent_digest: str = "") -> str:
        payload = json.dumps({"type": "workdir", "workdir": self.workdir, "parent": parent_digest}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()


class EnvStep(BaseModel):
    """Set environment variables that persist into the final image."""

    name: str = ""
    env: dict[str, str]

    def cache_key(self, parent_digest: str = "") -> str:
        payload = json.dumps(
            {"type": "env", "env": self.env, "parent": parent_digest}, sort_keys=True
        )
        return hashlib.sha256(payload.encode()).hexdigest()


# Union type for all build step kinds
BuildStep = Union[RunStep, CopyStep, WorkdirStep, EnvStep]


# ------------------------------------------------------------------
# Top-level sections
# ------------------------------------------------------------------

class BoxMeta(BaseModel):
    """Optional metadata about the image."""

    author: str = ""
    description: str = ""
    labels: dict[str, str] = Field(default_factory=dict)


class BoxSection(BaseModel):
    """[box] section — image identity and base."""

    name: str
    version: str = "latest"
    base: str  # Base image reference, e.g. "ubuntu:24.04"
    meta: BoxMeta = Field(default_factory=BoxMeta)


class PythonSpec(BaseModel):
    """[python] section — Python environment to install in the image."""

    version: str = "3.12"
    packages: list[str] = Field(default_factory=list)
    venv_path: str = "/opt/pybox-venv"


class RunSpec(BaseModel):
    """[run] section — runtime defaults (CMD, ENV, exposed ports, user)."""

    cmd: list[str] = Field(default_factory=list)
    expose: list[int] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    user: str = ""


class HealthcheckSpec(BaseModel):
    """[healthcheck] section."""

    cmd: list[str] = Field(default_factory=list)
    interval: str = "30s"
    timeout: str = "5s"
    retries: int = 3


class LimitsSpec(BaseModel):
    """[limits] section — maps to CgroupSpec fields."""

    memory: str | None = None
    cpu: float | None = None
    pids: int | None = None


class BuildSpec(BaseModel):
    """Complete validated build specification parsed from a boxfile.toml.

    All sections except [box] are optional.
    """

    box: BoxSection
    python: PythonSpec | None = None
    steps: list[BuildStep] = Field(default_factory=list)
    run: RunSpec = Field(default_factory=RunSpec)
    limits: LimitsSpec | None = None
    healthcheck: HealthcheckSpec | None = None

    def to_cgroup_spec(self):  # type: ignore[return]
        """Convert the [limits] section to a CgroupSpec, or None."""
        if self.limits is None:
            return None
        from pybox.cgroups.specs import CgroupSpec
        return CgroupSpec(
            memory=self.limits.memory,
            cpu=self.limits.cpu,
            pids=self.limits.pids,
        )


def _parse_step(raw: dict) -> BuildStep:
    """Parse a raw step dict into the correct BuildStep subtype.

    Args:
        raw: Dict from TOML [[steps]] array.

    Returns:
        One of RunStep, CopyStep, WorkdirStep, EnvStep.

    Raises:
        ValueError: If no recognised step field is present.
    """
    if "run" in raw:
        return RunStep.model_validate(raw)
    if "copy" in raw:
        return CopyStep.model_validate(raw)
    if "workdir" in raw:
        return WorkdirStep.model_validate(raw)
    if "env" in raw:
        return EnvStep.model_validate(raw)
    raise ValueError(f"Unrecognised build step (no run/copy/workdir/env field): {raw}")
