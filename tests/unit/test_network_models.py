"""Unit tests for pybox.network.models."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pybox.network.models import Network, NetworkEndpoint


class TestNetwork:
    def _make_network(self) -> Network:
        return Network(
            id="abc123",
            name="pybox0",
            cidr="172.16.0.0/24",
            gateway="172.16.0.1",
            driver="bridge",
            bridge="pybox0",
        )

    def test_serialise_to_json(self) -> None:
        net = self._make_network()
        data = json.loads(net.model_dump_json())
        assert data["id"] == "abc123"
        assert data["cidr"] == "172.16.0.0/24"
        assert data["gateway"] == "172.16.0.1"

    def test_deserialise_from_json(self) -> None:
        net = self._make_network()
        json_str = net.model_dump_json()
        net2 = Network.model_validate_json(json_str)
        assert net2.id == net.id
        assert net2.cidr == net.cidr

    def test_save_and_load(self, tmp_path: Path) -> None:
        net = self._make_network()
        net.save(tmp_path)
        assert (tmp_path / "pybox0.json").exists()

        loaded = Network.load(tmp_path, "pybox0")
        assert loaded.id == net.id
        assert loaded.gateway == net.gateway

    def test_load_nonexistent_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            Network.load(tmp_path, "nonexistent")

    def test_list_all_empty(self, tmp_path: Path) -> None:
        assert Network.list_all(tmp_path) == []

    def test_list_all_returns_saved_networks(self, tmp_path: Path) -> None:
        net1 = Network(id="a", name="net1", cidr="10.0.0.0/24", gateway="10.0.0.1")
        net2 = Network(id="b", name="net2", cidr="10.1.0.0/24", gateway="10.1.0.1")
        net1.save(tmp_path)
        net2.save(tmp_path)

        networks = Network.list_all(tmp_path)
        names = {n.name for n in networks}
        assert "net1" in names
        assert "net2" in names

    def test_list_all_skips_ipam_json(self, tmp_path: Path) -> None:
        (tmp_path / "ipam.json").write_text('{"allocated": []}')
        net = Network(id="c", name="test", cidr="10.2.0.0/24", gateway="10.2.0.1")
        net.save(tmp_path)

        networks = Network.list_all(tmp_path)
        assert len(networks) == 1  # ipam.json is excluded


class TestNetworkEndpoint:
    def _make_endpoint(self, container_id: str = "abc123def456") -> NetworkEndpoint:
        return NetworkEndpoint(
            container_id=container_id,
            ip="172.16.0.5",
            veth_host="veth-abc123de",
            veth_container="eth0",
        )

    def test_serialise_to_json(self) -> None:
        ep = self._make_endpoint()
        data = json.loads(ep.model_dump_json())
        assert data["container_id"] == "abc123def456"
        assert data["ip"] == "172.16.0.5"

    def test_save_and_load(self, tmp_path: Path) -> None:
        ep = self._make_endpoint()
        ep.save(tmp_path)
        assert (tmp_path / "endpoints" / "abc123def456.json").exists()

        loaded = NetworkEndpoint.load(tmp_path, "abc123def456")
        assert loaded.ip == ep.ip
        assert loaded.veth_host == ep.veth_host

    def test_load_nonexistent_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            NetworkEndpoint.load(tmp_path, "doesnotexist")

    def test_remove_deletes_file(self, tmp_path: Path) -> None:
        ep = self._make_endpoint()
        ep.save(tmp_path)
        ep.remove(tmp_path)
        assert not (tmp_path / "endpoints" / "abc123def456.json").exists()

    def test_remove_nonexistent_is_noop(self, tmp_path: Path) -> None:
        ep = self._make_endpoint()
        ep.remove(tmp_path)  # Should not raise
