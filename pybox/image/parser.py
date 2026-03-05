"""Parse and validate a boxfile.toml into a BuildSpec.

Uses the stdlib tomllib (Python 3.11+) for reading. Pydantic v2 handles
field validation and type coercion via BuildSpec.

Usage:
    spec = parse_boxfile(Path("boxfile.toml"))
    print(spec.box.base)   # "ubuntu:24.04"
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from pybox.image.build_spec import BuildSpec, _parse_step


def parse_boxfile(path: Path) -> BuildSpec:
    """Parse a boxfile.toml and return a validated BuildSpec.

    Args:
        path: Path to the boxfile.toml file.

    Returns:
        Validated BuildSpec instance.

    Raises:
        FileNotFoundError: If the path does not exist.
        tomllib.TOMLDecodeError: If the TOML is invalid.
        pydantic.ValidationError: If required fields are missing or invalid.
    """
    if not path.exists():
        raise FileNotFoundError(f"boxfile not found: {path}")

    # tomllib requires binary mode
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    # Parse steps separately using the discriminated union helper
    raw_steps = raw.pop("steps", [])
    parsed_steps = [_parse_step(s) for s in raw_steps]

    spec = BuildSpec.model_validate(raw)
    # Attach the parsed steps (can't pass via model_validate directly because
    # the discriminated union needs the helper)
    object.__setattr__(spec, "steps", parsed_steps)

    return spec
