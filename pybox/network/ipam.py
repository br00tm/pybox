"""IP Address Management for PyBox container networks.

Maintains a pool of available IPs in a CIDR block and allocates unique
addresses to containers. State is persisted to a JSON file to survive
daemon restarts.

Default pool: 172.16.0.0/24, gateway: 172.16.0.1
Container IPs start at .2 (.1 is reserved for the gateway).

Thread/process safety: uses a simple file-based lock via a .lock sentinel.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
from pathlib import Path

from pybox.exceptions import NetworkError

logger = logging.getLogger(__name__)

DEFAULT_CIDR = "172.16.0.0/24"
DEFAULT_GATEWAY = "172.16.0.1"


class IpamManager:
    """Allocates and releases IP addresses from a CIDR pool.

    Args:
        networks_dir: Directory where ipam.json is stored.
        cidr:         Network CIDR block, e.g. "172.16.0.0/24".
        gateway:      Gateway address to reserve (excluded from pool).
    """

    def __init__(
        self,
        networks_dir: Path,
        cidr: str = DEFAULT_CIDR,
        gateway: str = DEFAULT_GATEWAY,
    ) -> None:
        self._networks_dir = networks_dir
        self._cidr = cidr
        self._gateway = gateway
        self._state_file = networks_dir / "ipam.json"
        self._lock_file = networks_dir / "ipam.lock"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def allocate(self) -> str:
        """Allocate the next available IP address from the pool.

        Returns:
            IP address string, e.g. "172.16.0.2".

        Raises:
            NetworkError: If the pool is exhausted.
        """
        with self._locked():
            allocated = self._load()
            network = ipaddress.IPv4Network(self._cidr, strict=False)

            for host in network.hosts():
                ip = str(host)
                if ip == self._gateway:
                    continue  # reserve gateway
                if ip not in allocated:
                    allocated.add(ip)
                    self._save(allocated)
                    logger.debug("IPAM allocated: %s", ip)
                    return ip

            raise NetworkError(
                f"IPAM pool exhausted for {self._cidr}",
                details=f"{len(allocated)} IPs in use",
            )

    def release(self, ip: str) -> None:
        """Release an IP back to the pool.

        Args:
            ip: IP address string to release.
        """
        with self._locked():
            allocated = self._load()
            allocated.discard(ip)
            self._save(allocated)
        logger.debug("IPAM released: %s", ip)

    def list_allocated(self) -> set[str]:
        """Return the set of currently allocated IPs."""
        return self._load()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> set[str]:
        """Load allocated IPs from the state file."""
        if not self._state_file.exists():
            return set()
        try:
            data = json.loads(self._state_file.read_text())
            return set(data.get("allocated", []))
        except (json.JSONDecodeError, OSError):
            return set()

    def _save(self, allocated: set[str]) -> None:
        """Persist allocated IPs to the state file."""
        self._networks_dir.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(
            json.dumps({"cidr": self._cidr, "allocated": sorted(allocated)}, indent=2)
        )

    class _locked:
        """Minimal file-based lock context manager."""

        def __init__(self, manager: "IpamManager") -> None:
            self._lock_file = manager._lock_file

        def __new__(cls, manager: "IpamManager"):  # type: ignore[override]
            # Return a regular context manager object
            obj = object.__new__(cls)
            obj._lock_file = manager._lock_file  # type: ignore[attr-defined]
            return obj

        def __enter__(self) -> None:
            self._lock_file.parent.mkdir(parents=True, exist_ok=True)
            # Simple busy-wait with up to 5 retries
            import time
            for _ in range(50):
                try:
                    fd = os.open(str(self._lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    os.close(fd)
                    return
                except FileExistsError:
                    time.sleep(0.1)
            raise NetworkError("IPAM lock timeout — another process may be holding it")

        def __exit__(self, *_: object) -> None:
            self._lock_file.unlink(missing_ok=True)
