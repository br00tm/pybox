"""Pydantic models for PyBox network state.

Network state is persisted to JSON files under:
    <PYBOX_ROOT>/networks/<name>.json          — per-network config
    <PYBOX_ROOT>/networks/ipam.json            — allocated IP pool
    <PYBOX_ROOT>/networks/endpoints/<id>.json  — per-container endpoint
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class Network(BaseModel):
    """A PyBox virtual network.

    Fields:
        id:      Unique network identifier (short hex).
        name:    Human-readable name (e.g. "pybox0").
        cidr:    Network CIDR block, e.g. "172.16.0.0/24".
        gateway: Gateway IP address, e.g. "172.16.0.1".
        driver:  Driver name ("bridge").
        bridge:  Host bridge interface name (e.g. "pybox0").
    """

    id: str
    name: str
    cidr: str
    gateway: str
    driver: str = "bridge"
    bridge: str = "pybox0"

    def save(self, networks_dir: Path) -> None:
        """Persist this network config to disk."""
        networks_dir.mkdir(parents=True, exist_ok=True)
        (networks_dir / f"{self.name}.json").write_text(
            self.model_dump_json(indent=2)
        )

    @classmethod
    def load(cls, networks_dir: Path, name: str) -> "Network":
        """Load a network config from disk."""
        path = networks_dir / f"{name}.json"
        if not path.exists():
            raise FileNotFoundError(f"Network '{name}' not found")
        return cls.model_validate_json(path.read_text())

    @classmethod
    def list_all(cls, networks_dir: Path) -> list["Network"]:
        """Load all saved networks."""
        if not networks_dir.exists():
            return []
        result: list["Network"] = []
        for f in networks_dir.glob("*.json"):
            if f.name == "ipam.json":
                continue
            try:
                result.append(cls.model_validate_json(f.read_text()))
            except Exception:
                continue
        return result


class NetworkEndpoint(BaseModel):
    """A container's attachment to a network.

    Fields:
        container_id:     Container ID.
        network_name:     Name of the network.
        ip:               Assigned IP address (e.g. "172.16.0.2").
        mac:              MAC address of the container interface.
        veth_host:        Name of the host-side veth interface.
        veth_container:   Name of the container-side veth interface.
    """

    container_id: str
    network_name: str = "pybox0"
    ip: str
    mac: str = ""
    veth_host: str
    veth_container: str

    def save(self, networks_dir: Path) -> None:
        """Persist this endpoint to disk."""
        ep_dir = networks_dir / "endpoints"
        ep_dir.mkdir(parents=True, exist_ok=True)
        (ep_dir / f"{self.container_id}.json").write_text(
            self.model_dump_json(indent=2)
        )

    @classmethod
    def load(cls, networks_dir: Path, container_id: str) -> "NetworkEndpoint":
        """Load an endpoint from disk."""
        path = networks_dir / "endpoints" / f"{container_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"No network endpoint for container {container_id}")
        return cls.model_validate_json(path.read_text())

    def remove(self, networks_dir: Path) -> None:
        """Delete the endpoint file."""
        path = networks_dir / "endpoints" / f"{self.container_id}.json"
        path.unlink(missing_ok=True)
