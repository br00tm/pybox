"""NetworkManager: orchestrates bridge, IPAM, veth, NAT, and DNS setup.

Responsible for:
    - Setting up the pybox0 bridge on first use
    - Assigning a unique IP to each container
    - Creating and configuring veth pairs
    - Adding NAT masquerade and port-forwarding rules
    - Writing resolv.conf into the container rootfs
    - Tearing all of this down on container stop/remove
"""

from __future__ import annotations

import ipaddress
import logging
from pathlib import Path

from pybox.exceptions import NetworkError
from pybox.network.bridge import BridgeManager
from pybox.network.dns import write_resolv_conf
from pybox.network.ipam import DEFAULT_CIDR, DEFAULT_GATEWAY, IpamManager
from pybox.network.models import Network, NetworkEndpoint
from pybox.network.nat import NatManager
from pybox.network.veth import VethManager

logger = logging.getLogger(__name__)

_DEFAULT_BRIDGE = "pybox0"
_BRIDGE_CIDR = f"{DEFAULT_GATEWAY}/24"


class NetworkManager:
    """Orchestrates all networking for PyBox containers.

    Args:
        networks_dir: Directory for network state persistence.
    """

    def __init__(self, networks_dir: Path) -> None:
        self._networks_dir = networks_dir
        self._ipam = IpamManager(networks_dir)
        self._bridge = BridgeManager(_DEFAULT_BRIDGE)
        self._veth = VethManager(_DEFAULT_BRIDGE)
        self._nat = NatManager()

    def ensure_bridge(self) -> None:
        """Create the default bridge and enable IP forwarding if needed."""
        try:
            self._bridge.create_bridge(_BRIDGE_CIDR)
            self._bridge.enable_ip_forward()
            self._nat.setup_masquerade(_DEFAULT_BRIDGE, DEFAULT_CIDR)
            # Save network config
            network = Network(
                id=_DEFAULT_BRIDGE,
                name=_DEFAULT_BRIDGE,
                cidr=DEFAULT_CIDR,
                gateway=DEFAULT_GATEWAY,
                bridge=_DEFAULT_BRIDGE,
            )
            network.save(self._networks_dir)
        except NetworkError as exc:
            logger.warning("Failed to ensure bridge: %s", exc)

    def setup(
        self,
        container_id: str,
        container_pid: int,
        rootfs: Path,
        port_forwards: list[str] | None = None,
    ) -> NetworkEndpoint:
        """Set up networking for a container.

        Args:
            container_id:   Container ID.
            container_pid:  PID of the container init process.
            rootfs:         Path to the container's rootfs (for resolv.conf).
            port_forwards:  List of "host_port:container_port" strings.

        Returns:
            NetworkEndpoint with the assigned IP and interface names.

        Raises:
            NetworkError: If any network setup step fails.
        """
        self.ensure_bridge()

        # Allocate IP
        ip = self._ipam.allocate()
        prefix_len = ipaddress.IPv4Network(DEFAULT_CIDR, strict=False).prefixlen
        ip_cidr = f"{ip}/{prefix_len}"

        # Create veth pair and configure it
        host_if, cont_if = self._veth.create_pair(container_id)
        self._veth.move_to_netns(cont_if, container_pid)
        self._veth.configure_container_if(container_pid, cont_if, ip_cidr, DEFAULT_GATEWAY)

        # Port forwarding
        for pf_spec in (port_forwards or []):
            self._setup_port_forward(pf_spec, ip)

        # DNS resolution
        write_resolv_conf(rootfs)

        endpoint = NetworkEndpoint(
            container_id=container_id,
            ip=ip,
            veth_host=host_if,
            veth_container="eth0",  # renamed inside netns
        )
        endpoint.save(self._networks_dir)

        logger.info("Network setup for container %s: ip=%s", container_id, ip)
        return endpoint

    def teardown(self, container_id: str) -> None:
        """Remove networking resources for a container.

        Args:
            container_id: Container ID.
        """
        try:
            endpoint = NetworkEndpoint.load(self._networks_dir, container_id)
            self._veth.delete_pair(endpoint.veth_host)
            self._ipam.release(endpoint.ip)
            endpoint.remove(self._networks_dir)
            logger.info("Network torn down for container %s", container_id)
        except FileNotFoundError:
            pass  # Container had no network endpoint
        except NetworkError as exc:
            logger.warning("Error tearing down network for %s: %s", container_id, exc)

    def list_networks(self) -> list[Network]:
        """Return all known networks."""
        return Network.list_all(self._networks_dir)

    def _setup_port_forward(self, pf_spec: str, container_ip: str) -> None:
        """Parse "host_port:container_port[/proto]" and add NAT rule."""
        proto = "tcp"
        if "/" in pf_spec:
            pf_spec, proto = pf_spec.rsplit("/", 1)

        parts = pf_spec.split(":")
        if len(parts) != 2:
            logger.warning("Invalid port forward spec: %s", pf_spec)
            return

        try:
            host_port = int(parts[0])
            cont_port = int(parts[1])
        except ValueError:
            logger.warning("Invalid port numbers in: %s", pf_spec)
            return

        self._nat.add_port_forward(host_port, container_ip, cont_port, proto)
