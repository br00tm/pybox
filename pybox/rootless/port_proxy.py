"""Pure-Python asyncio port forwarder for rootless containers.

Proxies TCP (and UDP) connections from a host port to a container IP/port
without requiring iptables or CAP_NET_ADMIN. Used in rootless mode where
the container's IP is only reachable within the slirp4netns network.

Usage:
    proxy = PortProxy()
    await proxy.forward(8080, "10.0.2.100", 80, "tcp")
    # ... container is serving on 10.0.2.100:80 ...
    await proxy.stop_all()
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_BUFFER = 65536


@dataclass
class _ProxyRule:
    """A single active port-forwarding rule."""
    host_port: int
    container_ip: str
    container_port: int
    proto: str
    server: asyncio.Server | None = field(default=None, repr=False)


class PortProxy:
    """Manages a set of asyncio-based port-forwarding rules.

    One asyncio server is started per TCP forwarding rule. UDP rules
    use a DatagramProtocol.

    Args:
        bind_addr: Host address to bind the proxy servers on.
    """

    def __init__(self, bind_addr: str = "0.0.0.0") -> None:
        self._bind_addr = bind_addr
        self._rules: list[_ProxyRule] = []

    async def forward(
        self,
        host_port: int,
        container_ip: str,
        container_port: int,
        proto: str = "tcp",
    ) -> None:
        """Start proxying connections from host_port → container_ip:container_port.

        Args:
            host_port:      Host port to listen on.
            container_ip:   Container destination IP.
            container_port: Container destination port.
            proto:          "tcp" or "udp".
        """
        rule = _ProxyRule(
            host_port=host_port,
            container_ip=container_ip,
            container_port=container_port,
            proto=proto,
        )

        if proto == "tcp":
            server = await asyncio.start_server(
                lambda r, w: self._handle_tcp(r, w, container_ip, container_port),
                self._bind_addr,
                host_port,
            )
            rule.server = server
            logger.info(
                "TCP proxy: %s:%d → %s:%d",
                self._bind_addr, host_port, container_ip, container_port,
            )
        elif proto == "udp":
            loop = asyncio.get_event_loop()
            transport, _ = await loop.create_datagram_endpoint(
                lambda: _UdpProxy(container_ip, container_port),
                local_addr=(self._bind_addr, host_port),
            )
            logger.info(
                "UDP proxy: %s:%d → %s:%d",
                self._bind_addr, host_port, container_ip, container_port,
            )

        self._rules.append(rule)

    async def stop_all(self) -> None:
        """Stop all active forwarding rules."""
        for rule in self._rules:
            if rule.server is not None:
                rule.server.close()
                await rule.server.wait_closed()
        self._rules.clear()
        logger.debug("All port proxies stopped")

    @staticmethod
    async def _handle_tcp(
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        container_ip: str,
        container_port: int,
    ) -> None:
        """Forward a single TCP connection to the container."""
        try:
            target_reader, target_writer = await asyncio.open_connection(
                container_ip, container_port
            )
        except OSError as exc:
            logger.warning(
                "Cannot connect to %s:%d: %s", container_ip, container_port, exc
            )
            client_writer.close()
            return

        async def _pipe(
            src: asyncio.StreamReader, dst: asyncio.StreamWriter
        ) -> None:
            try:
                while True:
                    data = await src.read(_BUFFER)
                    if not data:
                        break
                    dst.write(data)
                    await dst.drain()
            except (OSError, asyncio.CancelledError):
                pass
            finally:
                try:
                    dst.close()
                except Exception:
                    pass

        await asyncio.gather(
            _pipe(client_reader, target_writer),
            _pipe(target_reader, client_writer),
            return_exceptions=True,
        )


class _UdpProxy(asyncio.DatagramProtocol):
    """Simple UDP proxy that forwards datagrams to a container."""

    def __init__(self, container_ip: str, container_port: int) -> None:
        self._container_ip = container_ip
        self._container_port = container_port
        self._transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:  # type: ignore[override]
        self._transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if self._transport:
            self._transport.sendto(data, (self._container_ip, self._container_port))
