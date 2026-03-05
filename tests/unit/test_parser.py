"""Unit tests for pybox.image.parser and pybox.image.build_spec."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from pybox.image.build_spec import (
    BuildSpec,
    CopyStep,
    EnvStep,
    RunStep,
    WorkdirStep,
    _parse_step,
)


# ------------------------------------------------------------------
# _parse_step
# ------------------------------------------------------------------

class TestParseStep:
    def test_run_step(self) -> None:
        step = _parse_step({"name": "install", "run": "apt-get install -y curl"})
        assert isinstance(step, RunStep)
        assert step.run == "apt-get install -y curl"

    def test_copy_step(self) -> None:
        step = _parse_step({"name": "copy", "copy": {"src": "./src", "dst": "/app"}})
        assert isinstance(step, CopyStep)
        assert step.copy.src == "./src"
        assert step.copy.dst == "/app"

    def test_workdir_step(self) -> None:
        step = _parse_step({"name": "wd", "workdir": "/app"})
        assert isinstance(step, WorkdirStep)
        assert step.workdir == "/app"

    def test_env_step(self) -> None:
        step = _parse_step({"name": "env", "env": {"FOO": "bar"}})
        assert isinstance(step, EnvStep)
        assert step.env == {"FOO": "bar"}

    def test_unknown_step_raises(self) -> None:
        with pytest.raises(ValueError, match="Unrecognised"):
            _parse_step({"name": "unknown", "unknown_key": "value"})


# ------------------------------------------------------------------
# cache_key determinism
# ------------------------------------------------------------------

class TestCacheKey:
    def test_run_step_cache_key_deterministic(self) -> None:
        step = RunStep(run="apt-get install -y curl")
        key1 = step.cache_key("parent123")
        key2 = step.cache_key("parent123")
        assert key1 == key2

    def test_run_step_cache_key_changes_on_command_change(self) -> None:
        step1 = RunStep(run="apt-get install -y curl")
        step2 = RunStep(run="apt-get install -y wget")
        assert step1.cache_key("parent") != step2.cache_key("parent")

    def test_cache_key_changes_on_parent_change(self) -> None:
        step = RunStep(run="echo hello")
        key1 = step.cache_key("parent_a")
        key2 = step.cache_key("parent_b")
        assert key1 != key2

    def test_copy_step_cache_key_includes_paths(self) -> None:
        step1 = CopyStep(copy={"src": "./src", "dst": "/app"})
        step2 = CopyStep(copy={"src": "./other", "dst": "/app"})
        assert step1.cache_key("p") != step2.cache_key("p")

    def test_workdir_step_cache_key(self) -> None:
        step = WorkdirStep(workdir="/app")
        key = step.cache_key("parent")
        assert len(key) == 64  # sha256 hex length

    def test_env_step_cache_key_order_insensitive(self) -> None:
        step1 = EnvStep(env={"A": "1", "B": "2"})
        step2 = EnvStep(env={"B": "2", "A": "1"})
        # json.dumps with sort_keys makes both keys identical
        assert step1.cache_key("p") == step2.cache_key("p")


# ------------------------------------------------------------------
# parse_boxfile
# ------------------------------------------------------------------

class TestParseBoxfile:
    def _write_boxfile(self, tmp_path: Path, content: str) -> Path:
        f = tmp_path / "boxfile.toml"
        f.write_text(content)
        return f

    def test_minimal_valid_boxfile(self, tmp_path: Path) -> None:
        from pybox.image.parser import parse_boxfile

        path = self._write_boxfile(
            tmp_path,
            '[box]\nname = "myapp"\nbase = "ubuntu:24.04"\n',
        )
        spec = parse_boxfile(path)
        assert spec.box.name == "myapp"
        assert spec.box.base == "ubuntu:24.04"

    def test_full_boxfile(self, tmp_path: Path) -> None:
        from pybox.image.parser import parse_boxfile

        content = """
[box]
name = "myapp"
version = "1.0.0"
base = "ubuntu:24.04"

[python]
version = "3.12"
packages = ["fastapi", "uvicorn"]

[[steps]]
name = "install deps"
run = "apt-get install -y curl"

[[steps]]
name = "copy src"
copy = { src = "./src", dst = "/app" }

[run]
cmd = ["uvicorn", "main:app"]
expose = [8000]
env = { PYTHONUNBUFFERED = "1" }

[limits]
memory = "256m"
cpu = 0.5
pids = 100

[healthcheck]
cmd = ["curl", "-f", "http://localhost:8000/health"]
interval = "30s"
"""
        path = self._write_boxfile(tmp_path, content)
        spec = parse_boxfile(path)

        assert spec.box.version == "1.0.0"
        assert spec.python is not None
        assert spec.python.version == "3.12"
        assert "fastapi" in spec.python.packages
        assert len(spec.steps) == 2
        assert isinstance(spec.steps[0], RunStep)
        assert isinstance(spec.steps[1], CopyStep)
        assert spec.run.cmd == ["uvicorn", "main:app"]
        assert spec.run.expose == [8000]
        assert spec.limits is not None
        assert spec.limits.cpu == 0.5
        assert spec.healthcheck is not None
        assert spec.healthcheck.retries == 3

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        from pybox.image.parser import parse_boxfile
        with pytest.raises(FileNotFoundError):
            parse_boxfile(tmp_path / "nonexistent.toml")

    def test_invalid_toml_raises(self, tmp_path: Path) -> None:
        from pybox.image.parser import parse_boxfile
        import tomllib
        path = self._write_boxfile(tmp_path, "NOT VALID TOML !!!")
        with pytest.raises(tomllib.TOMLDecodeError):
            parse_boxfile(path)

    def test_missing_required_field_raises(self, tmp_path: Path) -> None:
        from pybox.image.parser import parse_boxfile
        from pydantic import ValidationError
        # Missing 'base' in [box]
        path = self._write_boxfile(tmp_path, '[box]\nname = "myapp"\n')
        with pytest.raises(ValidationError):
            parse_boxfile(path)

    def test_to_cgroup_spec(self, tmp_path: Path) -> None:
        from pybox.image.parser import parse_boxfile
        path = self._write_boxfile(
            tmp_path,
            '[box]\nname = "x"\nbase = "ubuntu"\n[limits]\nmemory = "256m"\ncpu = 0.5\n',
        )
        spec = parse_boxfile(path)
        cg = spec.to_cgroup_spec()
        assert cg is not None
        assert cg.cpu == 0.5
        assert cg.memory == 256 * 1024 * 1024
