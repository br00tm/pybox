"""NAT/masquerade rules for container outbound internet access.

Uses nftables via subprocess. Creates a table named 'pybox' with:
    - POSTROUTING chain: masquerade traffic from the container CIDR
    - PREROUTING chain: DNAT for port forwarding rules

Port forwarding format: "8080:80" → host port 8080 → container port 80.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass

from pybox.exceptions import NetworkError

logger = logging.getLogger(__name__)

_NFT_TABLE = "pybox"


@dataclass
class PortForward:
    """A single port-forwarding rule."""
    host_port: int
    container_ip: str
    container_port: int
    proto: str = "tcp"  # "tcp" or "udp"


class NatManager:
    """Manages nftables NAT rules for PyBox container networking."""

    def setup_masquerade(self, bridge_iface: str, cidr: str) -> None:
        """Create a masquerade rule for outbound container traffic.

        Args:
            bridge_iface: Host bridge interface (e.g. "pybox0").
            cidr:         Container network CIDR (e.g. "172.16.0.0/24").

        Raises:
            NetworkError: If nftables commands fail.
        """
        # Create the pybox nftables table if it doesn't exist
        nft_script = f"""
table ip {_NFT_TABLE} {{
    chain POSTROUTING {{
        type nat hook postrouting priority srcnat;
        ip saddr {cidr} masquerade
    }}
    chain PREROUTING {{
        type nat hook prerouting priority dstnat;
    }}
}}
"""
        self._run_nft(nft_script)
        logger.info("NAT masquerade rule added for %s (%s)", bridge_iface, cidr)

    def add_port_forward(
        self,
        host_port: int,
        container_ip: str,
        container_port: int,
        proto: str = "tcp",
    ) -> None:
        """Add a DNAT port-forwarding rule.

        Redirects traffic arriving on host_port to container_ip:container_port.

        Args:
            host_port:      Host port to listen on.
            container_ip:   Container destination IP.
            container_port: Container destination port.
            proto:          Protocol ("tcp" or "udp").

        Raises:
            NetworkError: If the nft command fails.
        """
        rule = (
            f"add rule ip {_NFT_TABLE} PREROUTING "
            f"{proto} dport {host_port} "
            f"dnat to {container_ip}:{container_port}"
        )
        self._run_nft_cmd(["nft", *rule.split()])
        logger.debug(
            "Port forward: host:%d → %s:%d/%s",
            host_port, container_ip, container_port, proto,
        )

    def cleanup(self) -> None:
        """Remove the entire pybox nftables table."""
        try:
            self._run_nft_cmd(["nft", "delete", "table", "ip", _NFT_TABLE])
            logger.info("nftables table '%s' removed", _NFT_TABLE)
        except NetworkError:
            pass  # Table may not exist

    def _run_nft(self, script: str) -> None:
        """Apply an nftables script via `nft -f -`."""
        try:
            subprocess.run(
                ["nft", "-f", "-"],
                input=script,
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise NetworkError(
                "nftables script failed",
                details=exc.stderr.strip(),
            ) from exc
        except FileNotFoundError as exc:
            raise NetworkError("nft not found — install nftables") from exc

    def _run_nft_cmd(self, cmd: list[str]) -> None:
        """Run a single nft command."""
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            raise NetworkError(
                f"nft command failed: {' '.join(cmd)}",
                details=exc.stderr.strip(),
            ) from exc
