"""Unit tests for pybox.network.ipam.IpamManager."""

from __future__ import annotations

from pathlib import Path

import pytest

from pybox.exceptions import NetworkError


class TestIpamManager:
    def _make_ipam(self, tmp_path: Path, cidr: str = "192.168.100.0/29"):
        from pybox.network.ipam import IpamManager
        return IpamManager(tmp_path, cidr=cidr, gateway="192.168.100.1")

    def test_allocates_unique_ips(self, tmp_path: Path) -> None:
        ipam = self._make_ipam(tmp_path)
        # /29 has 6 usable hosts (.1-.6); .1 is gateway, so 5 allocatable
        ip1 = ipam.allocate()
        ip2 = ipam.allocate()
        assert ip1 != ip2
        assert ip1.startswith("192.168.100.")
        assert ip2.startswith("192.168.100.")

    def test_does_not_allocate_gateway(self, tmp_path: Path) -> None:
        ipam = self._make_ipam(tmp_path)
        ip = ipam.allocate()
        assert ip != "192.168.100.1"

    def test_raises_when_pool_exhausted(self, tmp_path: Path) -> None:
        from pybox.network.ipam import IpamManager
        # /30 has only 2 usable hosts (.1 and .2); .1 is gateway → 1 allocatable
        ipam = IpamManager(tmp_path / "exhaust", cidr="192.168.100.0/30", gateway="192.168.100.1")
        ipam.allocate()
        with pytest.raises(NetworkError, match="exhausted"):
            ipam.allocate()

    def test_release_allows_reallocation(self, tmp_path: Path) -> None:
        ipam = self._make_ipam(tmp_path)
        ip1 = ipam.allocate()
        ipam.allocate()
        # Pool full — release one
        ipam.release(ip1)
        ip3 = ipam.allocate()
        assert ip3 == ip1

    def test_persists_state_across_instances(self, tmp_path: Path) -> None:
        from pybox.network.ipam import IpamManager

        ipam1 = IpamManager(tmp_path, cidr="192.168.100.0/29", gateway="192.168.100.1")
        ip = ipam1.allocate()

        # New instance reading the same state file
        ipam2 = IpamManager(tmp_path, cidr="192.168.100.0/29", gateway="192.168.100.1")
        allocated = ipam2.list_allocated()
        assert ip in allocated

    def test_list_allocated_empty_initially(self, tmp_path: Path) -> None:
        ipam = self._make_ipam(tmp_path)
        assert ipam.list_allocated() == set()

    def test_release_nonexistent_is_noop(self, tmp_path: Path) -> None:
        ipam = self._make_ipam(tmp_path)
        ipam.release("192.168.100.99")  # Should not raise

    def test_larger_pool(self, tmp_path: Path) -> None:
        from pybox.network.ipam import IpamManager

        ipam = IpamManager(tmp_path, cidr="10.0.0.0/24", gateway="10.0.0.1")
        ips = {ipam.allocate() for _ in range(10)}
        assert len(ips) == 10  # All unique
        assert "10.0.0.1" not in ips  # Gateway not allocated
