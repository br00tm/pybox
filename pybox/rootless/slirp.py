"""slirp4netns-based networking for rootless containers.

slirp4netns provides network access to user-namespace containers without
requiring CAP_NET_ADMIN. It implements a userspace TCP/IP stack connected
to the container via a TAP device.

Reference: https://github.com/rootless-containers/slirp4netns
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from pathlib import Path

from pybox.exceptions import NetworkError

logger = logging.getLogger(__name__)

# Path for slirp4netns API socket (per-container)
_SLIRP_SOCK_DIR = Path("/run/pybox/slirp")


class SlirpNetworkManager:
    """Manages slirp4netns processes for rootless container networking.

    Each container gets its own slirp4netns process that creates a TAP
    device inside the container's network namespace and connects it to
    a userspace TCP/IP stack running in the host process.

    Args:
        containers_dir: Container state directory for storing process info.
    """

    def __init__(self, containers_dir: Path) -> None:
        self._containers_dir = containers_dir
        self._processes: dict[str, subprocess.Popen] = {}

    def setup(
        self,
        container_id: str,
        pid: int,
        port_forwards: list[str] | None = None,
    ) -> str:
        """Start slirp4netns for a container and return the assigned IP.

        Args:
            container_id:  Container ID.
            pid:           PID of the container init process.
            port_forwards: List of "host_port:container_port" strings.

        Returns:
            IP address assigned to the container (e.g. "10.0.2.100").

        Raises:
            NetworkError: If slirp4netns is not available or fails to start.
        """
        if not shutil.which("slirp4netns"):
            raise NetworkError(
                "slirp4netns not found. Install with: apt install slirp4netns"
            )

        _SLIRP_SOCK_DIR.mkdir(parents=True, exist_ok=True)
        api_sock = _SLIRP_SOCK_DIR / f"{container_id}.sock"

        cmd = [
            "slirp4netns",
            "--configure",          # Configure the tap device automatically
            "--mtu=65520",          # Jumbo MTU for performance
            "--disable-host-loopback",  # Isolate from host loopback
            f"--api-socket={api_sock}",
            str(pid),
            "tap0",                 # Device name inside the container netns
        ]

        logger.debug("Starting slirp4netns: %s", " ".join(cmd))
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise NetworkError("slirp4netns not found", details=str(exc)) from exc

        self._processes[container_id] = proc

        # Wait for slirp4netns to initialise (it writes to stdout when ready)
        if not self._wait_ready(proc, timeout=5.0):
            proc.kill()
            raise NetworkError(f"slirp4netns failed to start for container {container_id}")

        # Set up port forwards via the API socket
        for pf in (port_forwards or []):
            self._add_port_forward_api(api_sock, pf)

        # slirp4netns default IP is 10.0.2.100 for the container
        container_ip = "10.0.2.100"
        logger.info("slirp4netns ready for container %s (IP: %s)", container_id, container_ip)
        return container_ip

    def teardown(self, container_id: str) -> None:
        """Stop the slirp4netns process for a container."""
        proc = self._processes.pop(container_id, None)
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            logger.debug("slirp4netns stopped for container %s", container_id)

        # Remove API socket
        api_sock = _SLIRP_SOCK_DIR / f"{container_id}.sock"
        api_sock.unlink(missing_ok=True)

    def _wait_ready(self, proc: subprocess.Popen, timeout: float = 5.0) -> bool:
        """Wait for slirp4netns to write 'ready' to stdout."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return False  # Process died
            if proc.stdout:
                line = proc.stdout.readline()
                if line:
                    logger.debug("slirp4netns: %s", line.decode().strip())
                    if b"ready" in line.lower():
                        return True
            time.sleep(0.1)
        return True  # Assume ready after timeout (some versions don't print "ready")

    def _add_port_forward_api(self, api_sock: Path, pf_spec: str) -> None:
        """Add a port forward via the slirp4netns API socket."""
        if not api_sock.exists():
            logger.warning("slirp4netns API socket not found: %s", api_sock)
            return

        parts = pf_spec.split(":")
        if len(parts) != 2:
            return

        try:
            host_port = int(parts[0])
            cont_port = int(parts[1])
        except ValueError:
            return

        # slirp4netns API: {"execute": "add_hostfwd", "arguments": {...}}
        request = json.dumps({
            "execute": "add_hostfwd",
            "arguments": {
                "proto": "tcp",
                "host_addr": "0.0.0.0",
                "host_port": host_port,
                "guest_port": cont_port,
            },
        })

        import socket
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.connect(str(api_sock))
                sock.sendall(request.encode() + b"\n")
                response = sock.recv(4096)
                logger.debug("slirp4netns port forward response: %s", response.decode().strip())
        except OSError as exc:
            logger.warning("Failed to add slirp4netns port forward: %s", exc)
