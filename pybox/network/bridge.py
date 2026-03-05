"""Host bridge interface management for PyBox container networking.

Creates and manages a Linux bridge (pybox0) that connects container veth
pairs and provides internet access via NAT masquerade.

All operations use subprocess with the `ip` command (approved exception
for network operations per ARCHITECTURE.md).
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from pybox.exceptions import NetworkError

logger = logging.getLogger(__name__)


class BridgeManager:
    """Manages the host-side bridge interface for PyBox containers.

    Args:
        bridge_name: Name of the bridge interface (default: "pybox0").
    """

    def __init__(self, bridge_name: str = "pybox0") -> None:
        self._bridge = bridge_name

    def exists(self) -> bool:
        """Return True if the bridge interface already exists."""
        result = subprocess.run(
            ["ip", "link", "show", self._bridge],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def create_bridge(self, cidr: str) -> None:
        """Create the bridge interface and assign it the gateway IP.

        Args:
            cidr: CIDR with gateway IP, e.g. "172.16.0.1/24".

        Raises:
            NetworkError: If any ip command fails.
        """
        if self.exists():
            logger.debug("Bridge %s already exists", self._bridge)
            return

        try:
            # Create the bridge interface
            self._run(["ip", "link", "add", self._bridge, "type", "bridge"])
            # Assign the gateway IP
            self._run(["ip", "addr", "add", cidr, "dev", self._bridge])
            # Bring the interface up
            self._run(["ip", "link", "set", self._bridge, "up"])
            logger.info("Bridge %s created with CIDR %s", self._bridge, cidr)
        except NetworkError:
            raise

    def delete_bridge(self) -> None:
        """Remove the bridge interface."""
        if not self.exists():
            return
        try:
            self._run(["ip", "link", "set", self._bridge, "down"])
            self._run(["ip", "link", "delete", self._bridge, "type", "bridge"])
            logger.info("Bridge %s deleted", self._bridge)
        except NetworkError as exc:
            logger.warning("Failed to delete bridge %s: %s", self._bridge, exc)

    def enable_ip_forward(self) -> None:
        """Enable IPv4 packet forwarding on the host.

        Required for containers to reach the internet via NAT masquerade.
        Writes 1 to /proc/sys/net/ipv4/ip_forward.
        """
        ip_forward = Path("/proc/sys/net/ipv4/ip_forward")
        try:
            ip_forward.write_text("1")
            logger.debug("IPv4 forwarding enabled")
        except OSError as exc:
            raise NetworkError(
                "Failed to enable IPv4 forwarding", details=str(exc)
            ) from exc

    def _run(self, cmd: list[str]) -> None:
        """Run an ip command; raise NetworkError on failure."""
        try:
            subprocess.run(
                cmd, check=True, capture_output=True, text=True
            )
        except subprocess.CalledProcessError as exc:
            raise NetworkError(
                f"Command failed: {' '.join(cmd)}",
                details=exc.stderr.strip(),
            ) from exc
