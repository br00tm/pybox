"""Virtual Ethernet pair management for container networking.

Each container gets a veth pair:
    veth-<id>  — host side, attached to pybox0 bridge
    ceth-<id>  — container side, moved into container's network namespace

After creation:
    - The host end is attached to the bridge and brought up.
    - The container end is moved into the container's netns via
      `ip link set ceth-<id> netns <pid>`.
    - Inside the netns: rename to eth0, assign IP, set up default route.
"""

from __future__ import annotations

import logging
import subprocess

from pybox.exceptions import NetworkError

logger = logging.getLogger(__name__)


class VethManager:
    """Manages veth pairs for container network attachment.

    Args:
        bridge_name: Bridge to attach the host-side veth to.
    """

    def __init__(self, bridge_name: str = "pybox0") -> None:
        self._bridge = bridge_name

    def create_pair(self, container_id: str) -> tuple[str, str]:
        """Create a veth pair for a container.

        Args:
            container_id: 12-char container ID.

        Returns:
            Tuple of (host_iface, container_iface) names.

        Raises:
            NetworkError: If any ip command fails.
        """
        short = container_id[:8]
        host_if = f"veth-{short}"
        cont_if = f"ceth-{short}"

        # Create the veth pair
        self._run([
            "ip", "link", "add", host_if,
            "type", "veth",
            "peer", "name", cont_if,
        ])

        # Attach host end to bridge
        self._run(["ip", "link", "set", host_if, "master", self._bridge])
        self._run(["ip", "link", "set", host_if, "up"])

        logger.debug("Created veth pair: %s ↔ %s", host_if, cont_if)
        return host_if, cont_if

    def move_to_netns(self, cont_if: str, pid: int) -> None:
        """Move the container-side interface into the container's network namespace.

        Args:
            cont_if: Container-side veth interface name.
            pid:     PID of the container init process.

        Raises:
            NetworkError: If the interface cannot be moved.
        """
        # ip link set <iface> netns <pid> — moves the interface into the
        # network namespace of the process with the given PID
        self._run(["ip", "link", "set", cont_if, "netns", str(pid)])
        logger.debug("Moved %s into netns of PID %d", cont_if, pid)

    def configure_container_if(
        self,
        pid: int,
        cont_if: str,
        ip_cidr: str,
        gateway: str,
    ) -> None:
        """Configure IP address and default route inside the container netns.

        Executes ip commands in the container's network namespace via
        `ip netns exec` (identified by PID).

        Args:
            pid:     Container init PID.
            cont_if: Container-side interface name (e.g. "ceth-abc12345").
            ip_cidr: IP/prefix for the container, e.g. "172.16.0.2/24".
            gateway: Default gateway IP, e.g. "172.16.0.1".
        """
        netns_prefix = ["nsenter", "-n", f"--target={pid}", "--"]

        # Rename container interface to eth0
        self._run([*netns_prefix, "ip", "link", "set", cont_if, "name", "eth0"])
        # Bring loopback up
        self._run([*netns_prefix, "ip", "link", "set", "lo", "up"])
        # Assign IP
        self._run([*netns_prefix, "ip", "addr", "add", ip_cidr, "dev", "eth0"])
        # Bring eth0 up
        self._run([*netns_prefix, "ip", "link", "set", "eth0", "up"])
        # Set default route
        self._run([*netns_prefix, "ip", "route", "add", "default", "via", gateway])

        logger.debug("Configured eth0 in PID %d netns: %s via %s", pid, ip_cidr, gateway)

    def delete_pair(self, host_if: str) -> None:
        """Remove a veth pair by deleting the host-side interface.

        Deleting one end of a veth pair automatically removes the other.
        """
        try:
            self._run(["ip", "link", "delete", host_if])
            logger.debug("Deleted veth pair (host if: %s)", host_if)
        except NetworkError as exc:
            logger.warning("Failed to delete veth %s: %s", host_if, exc)

    def _run(self, cmd: list[str]) -> None:
        """Run a command; raise NetworkError on failure."""
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            raise NetworkError(
                f"Command failed: {' '.join(cmd)}",
                details=exc.stderr.strip(),
            ) from exc
