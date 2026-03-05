"""Unit tests for pybox.cgroups modules.

Tests run without root — CgroupV2 file writes use tmp_path fixture.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pybox.cgroups.specs import CgroupSpec
from pybox.exceptions import CgroupError


# ------------------------------------------------------------------
# CgroupSpec — memory parsing
# ------------------------------------------------------------------

class TestCgroupSpecMemoryParsing:
    def test_parse_megabytes_lowercase(self) -> None:
        spec = CgroupSpec(memory="256m")
        assert spec.memory == 256 * 1024 * 1024

    def test_parse_megabytes_uppercase(self) -> None:
        spec = CgroupSpec(memory="512M")
        assert spec.memory == 512 * 1024 * 1024

    def test_parse_gigabytes(self) -> None:
        spec = CgroupSpec(memory="1g")
        assert spec.memory == 1024 * 1024 * 1024

    def test_parse_gigabytes_uppercase(self) -> None:
        spec = CgroupSpec(memory="2G")
        assert spec.memory == 2 * 1024 * 1024 * 1024

    def test_parse_kilobytes(self) -> None:
        spec = CgroupSpec(memory="512k")
        assert spec.memory == 512 * 1024

    def test_parse_bytes_int(self) -> None:
        spec = CgroupSpec(memory=1048576)
        assert spec.memory == 1048576

    def test_parse_invalid_string_raises(self) -> None:
        with pytest.raises(Exception):
            CgroupSpec(memory="invalid")

    def test_memory_max_value_with_limit(self) -> None:
        spec = CgroupSpec(memory="256m")
        assert spec.memory_max_value == str(256 * 1024 * 1024)

    def test_memory_max_value_no_limit(self) -> None:
        spec = CgroupSpec()
        assert spec.memory_max_value == "max"


# ------------------------------------------------------------------
# CgroupSpec — cpu_quota
# ------------------------------------------------------------------

class TestCgroupSpecCpuQuota:
    def test_cpu_half_core(self) -> None:
        spec = CgroupSpec(cpu=0.5)
        assert spec.cpu_max_value == "50000 100000"

    def test_cpu_full_core(self) -> None:
        spec = CgroupSpec(cpu=1.0)
        assert spec.cpu_max_value == "100000 100000"

    def test_cpu_quarter_core(self) -> None:
        spec = CgroupSpec(cpu=0.25)
        assert spec.cpu_max_value == "25000 100000"

    def test_cpu_no_limit(self) -> None:
        spec = CgroupSpec()
        assert spec.cpu_max_value == "max 100000"

    def test_cpu_below_zero_raises(self) -> None:
        with pytest.raises(Exception):
            CgroupSpec(cpu=-0.1)

    def test_cpu_above_one_raises(self) -> None:
        with pytest.raises(Exception):
            CgroupSpec(cpu=1.5)


# ------------------------------------------------------------------
# CgroupSpec — pids
# ------------------------------------------------------------------

class TestCgroupSpecPids:
    def test_pids_max_value_set(self) -> None:
        spec = CgroupSpec(pids=50)
        assert spec.pids_max_value == "50"

    def test_pids_max_value_none(self) -> None:
        spec = CgroupSpec()
        assert spec.pids_max_value == "max"

    def test_pids_zero_raises(self) -> None:
        with pytest.raises(Exception):
            CgroupSpec(pids=0)


# ------------------------------------------------------------------
# CgroupV2 — file writes using tmp_path
# ------------------------------------------------------------------

class TestCgroupV2:
    def _make_cgroup(self, tmp_path: Path, container_id: str = "abc123"):
        from pybox.cgroups.v2 import CgroupV2
        # Use tmp_path as cgroup root so no root privileges are needed
        return CgroupV2(container_id, tmp_path)

    def test_create_makes_directory(self, tmp_path: Path) -> None:
        cg = self._make_cgroup(tmp_path)
        cg.create()
        assert (tmp_path / "abc123").is_dir()

    def test_apply_limits_writes_memory_max(self, tmp_path: Path) -> None:
        cg = self._make_cgroup(tmp_path)
        cg.create()
        spec = CgroupSpec(memory="256m")
        cg.apply_limits(spec)
        content = (tmp_path / "abc123" / "memory.max").read_text()
        assert content == str(256 * 1024 * 1024)

    def test_apply_limits_writes_cpu_max(self, tmp_path: Path) -> None:
        cg = self._make_cgroup(tmp_path)
        cg.create()
        spec = CgroupSpec(cpu=0.5)
        cg.apply_limits(spec)
        content = (tmp_path / "abc123" / "cpu.max").read_text()
        assert content == "50000 100000"

    def test_apply_limits_writes_pids_max(self, tmp_path: Path) -> None:
        cg = self._make_cgroup(tmp_path)
        cg.create()
        spec = CgroupSpec(pids=100)
        cg.apply_limits(spec)
        content = (tmp_path / "abc123" / "pids.max").read_text()
        assert content == "100"

    def test_apply_limits_unlimited(self, tmp_path: Path) -> None:
        cg = self._make_cgroup(tmp_path)
        cg.create()
        spec = CgroupSpec()
        cg.apply_limits(spec)
        assert (tmp_path / "abc123" / "memory.max").read_text() == "max"
        assert (tmp_path / "abc123" / "cpu.max").read_text() == "max 100000"
        assert (tmp_path / "abc123" / "pids.max").read_text() == "max"

    def test_add_pid_writes_to_cgroup_procs(self, tmp_path: Path) -> None:
        cg = self._make_cgroup(tmp_path)
        cg.create()
        cg.add_pid(12345)
        content = (tmp_path / "abc123" / "cgroup.procs").read_text()
        assert content == "12345"

    def test_delete_removes_directory(self, tmp_path: Path) -> None:
        cg = self._make_cgroup(tmp_path)
        cg.create()
        cg.delete()
        assert not (tmp_path / "abc123").exists()

    def test_delete_nonexistent_is_noop(self, tmp_path: Path) -> None:
        cg = self._make_cgroup(tmp_path)
        # Should not raise even if directory doesn't exist
        cg.delete()

    def test_write_failure_raises_cgroup_error(self, tmp_path: Path) -> None:
        cg = self._make_cgroup(tmp_path)
        cg.create()
        # Make the directory read-only so writes fail
        (tmp_path / "abc123").chmod(0o555)
        try:
            with pytest.raises(CgroupError):
                cg._write("memory.max", "1000")
        finally:
            (tmp_path / "abc123").chmod(0o755)
