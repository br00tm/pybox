"""Async client for the PyBox daemon Unix socket.

Used by CLI commands to communicate with pyboxd rather than operating
on container state directly. This enables multi-client access and
consistent state management.

Usage:
    async with DaemonClient() as client:
        containers = await client.call(Method.PS)
        async for chunk in client.stream(Method.LOGS, container_id="abc"):
            sys.stdout.buffer.write(chunk)
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, AsyncIterator

from pybox.daemon.protocol import (
    DaemonRequest,
    Method,
    decode_response,
    encode,
    read_message,
)
from pybox.exceptions import ContainerError

logger = logging.getLogger(__name__)

DEFAULT_SOCKET = Path("/run/pybox/pyboxd.sock")


class DaemonClient:
    """Async IPC client for pyboxd.

    Args:
        socket_path: Path to the pyboxd Unix domain socket.
    """

    def __init__(self, socket_path: Path = DEFAULT_SOCKET) -> None:
        self._socket_path = socket_path
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._request_id = 0

    async def connect(self) -> None:
        """Open the Unix domain socket connection to pyboxd."""
        try:
            self._reader, self._writer = await asyncio.open_unix_connection(
                str(self._socket_path)
            )
            logger.debug("Connected to pyboxd at %s", self._socket_path)
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            raise ContainerError(
                f"Cannot connect to pyboxd at {self._socket_path}. "
                "Is pyboxd running? Try: pyboxd &",
                details=str(exc),
            ) from exc

    async def disconnect(self) -> None:
        """Close the socket connection."""
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None

    async def __aenter__(self) -> "DaemonClient":
        await self.connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.disconnect()

    async def call(self, method: Method, **params: Any) -> Any:
        """Send a request and return the response result.

        Args:
            method: RPC method to call.
            **params: Parameters passed in the request.

        Returns:
            The result field from the DaemonResponse.

        Raises:
            ContainerError: If the daemon returns an error.
        """
        assert self._writer is not None and self._reader is not None

        self._request_id += 1
        req = DaemonRequest(id=self._request_id, method=method, params=params)
        self._writer.write(encode(req))
        await self._writer.drain()

        raw = await read_message(self._reader)
        resp = decode_response(raw)

        if not resp.ok:
            raise ContainerError(f"Daemon error: {resp.error}")

        return resp.result

    async def stream(self, method: Method, **params: Any) -> AsyncIterator[bytes]:
        """Send a streaming request and yield response chunks.

        Streaming responses send multiple DaemonResponse messages.
        The stream ends when a response with result=None is received.

        Args:
            method:   RPC method (e.g. Method.LOGS).
            **params: Method parameters.

        Yields:
            Raw bytes chunks from the daemon stream.
        """
        assert self._writer is not None and self._reader is not None

        self._request_id += 1
        req = DaemonRequest(id=self._request_id, method=method, params=params)
        self._writer.write(encode(req))
        await self._writer.drain()

        while True:
            raw = await read_message(self._reader)
            resp = decode_response(raw)

            if not resp.ok:
                raise ContainerError(f"Daemon stream error: {resp.error}")

            if resp.result is None:
                return  # End of stream sentinel

            if isinstance(resp.result, bytes):
                yield resp.result
            elif isinstance(resp.result, str):
                yield resp.result.encode()
