"""Pydantic model for container resource limit specifications."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator, model_validator


def _parse_memory_string(value: str | int) -> int:
    """Parse a human-readable memory string to bytes.

    Accepts:
        - Plain integer (already in bytes): 268435456
        - Suffixed strings: "256m", "256M", "1g", "1G", "512k", "512K"

    Returns:
        Memory limit in bytes as an integer.

    Raises:
        ValueError: If the format is unrecognised.
    """
    if isinstance(value, int):
        return value

    pattern = re.compile(r"^(\d+(?:\.\d+)?)\s*([kmgKMG]?)$")
    m = pattern.match(value.strip())
    if not m:
        raise ValueError(f"Cannot parse memory string: {value!r}")

    number = float(m.group(1))
    suffix = m.group(2).lower()

    multipliers = {"k": 1024, "m": 1024**2, "g": 1024**3, "": 1}
    return int(number * multipliers[suffix])


class CgroupSpec(BaseModel):
    """Resource limits applied to a container's cgroup.

    All fields are optional — omitted fields mean "no limit" (cgroup max).

    Fields:
        memory: Memory limit, e.g. "256m", "1g". Stored as bytes internally.
        cpu:    CPU quota as a fraction of one core [0.0, 1.0].
                0.5 → container gets at most 50% of one CPU core.
        pids:   Maximum number of processes/threads in the container.
    """

    memory: int | None = Field(
        default=None,
        description="Memory limit in bytes (use parse_memory_string for human-readable input)",
        ge=0,
    )
    cpu: float | None = Field(
        default=None,
        description="CPU fraction [0.0, 1.0]. 0.5 = 50% of one core.",
        ge=0.0,
        le=1.0,
    )
    pids: int | None = Field(
        default=None,
        description="Maximum number of PIDs/threads in the container.",
        ge=1,
    )

    @field_validator("memory", mode="before")
    @classmethod
    def parse_memory(cls, v: str | int | None) -> int | None:
        """Accept human-readable memory strings like '256m' or '1g'."""
        if v is None:
            return None
        return _parse_memory_string(v)

    @model_validator(mode="after")
    def at_least_one_limit_or_empty(self) -> "CgroupSpec":
        """Allow a fully empty spec (all None) — means unlimited."""
        return self

    @property
    def memory_max_value(self) -> str:
        """Value to write to memory.max cgroup file.

        Returns 'max' if no memory limit is set, or the byte count as a string.
        """
        if self.memory is None:
            return "max"
        return str(self.memory)

    @property
    def cpu_max_value(self) -> str:
        """Value to write to cpu.max cgroup file.

        Format: "$QUOTA $PERIOD"
        Period is always 100000 microseconds (100ms, Linux default).
        Quota = cpu * period. "max 100000" means unlimited.
        """
        if self.cpu is None:
            return "max 100000"
        # cpu.max format: '<quota_us> <period_us>'
        # e.g. 0.5 CPU → 50000 / 100000 → 50% of one core per 100ms window
        period_us = 100_000
        quota_us = int(self.cpu * period_us)
        return f"{quota_us} {period_us}"

    @property
    def pids_max_value(self) -> str:
        """Value to write to pids.max cgroup file."""
        if self.pids is None:
            return "max"
        return str(self.pids)
