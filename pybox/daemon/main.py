"""pyboxd — PyBox daemon main entry point.

Runs an asyncio Unix domain socket server that handles JSON-RPC-style
requests from the pybox CLI. All container operations are serialised
through this daemon to maintain consistent state.

Socket: /run/pybox/pyboxd.sock (configurable via PYBOX_SOCKET env var)

Crash recovery: on startup, any container marked RUNNING in state files
is transitioned to STOPPED (since the daemon was not running to monitor
them).
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path
from typing import Any


from pybox.config import get_config
from pybox.container.runtime import ContainerManager
from pybox.container.state import ContainerState, StateManager
from pybox.daemon.protocol import (
    DaemonRequest,
    DaemonResponse,
    Method,
    decode_request,
    encode,
    read_message,
)

logger = logging.getLogger(__name__)

DEFAULT_SOCKET = Path(os.environ.get("PYBOX_SOCKET", "/run/pybox/pyboxd.sock"))


class PyBoxDaemon:
    """asyncio Unix socket server for pyboxd.

    Args:
        socket_path: Path to the Unix domain socket.
    """

    def __init__(self, socket_path: Path = DEFAULT_SOCKET) -> None:
        self._socket_path = socket_path
        self._cfg = get_config()
        self._manager = ContainerManager(self._cfg)
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        """Start the daemon server."""
        # Crash recovery: mark any previously-running containers as stopped
        self._recover_state()

        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._socket_path.unlink(missing_ok=True)

        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=str(self._socket_path),
        )
        self._socket_path.chmod(0o660)

        logger.info("pyboxd listening at %s", self._socket_path)
        async with self._server:
            await self._server.serve_forever()

    def _recover_state(self) -> None:
        """Mark any RUNNING containers as STOPPED on daemon startup."""
        state_mgr = StateManager(self._cfg.containers_dir)
        for container in state_mgr.list_all():
            if container.get("state") == ContainerState.RUNNING.value:
                container_id = container["id"]
                try:
                    state_mgr.set_state(container_id, ContainerState.STOPPED)
                    logger.info("Recovery: marked container %s as STOPPED", container_id)
                except Exception as exc:
                    logger.warning("Recovery failed for %s: %s", container_id, exc)

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle a single client connection."""
        peer = writer.get_extra_info("peername", "unknown")
        logger.debug("Client connected: %s", peer)
        try:
            while True:
                try:
                    raw = await read_message(reader)
                except (asyncio.IncompleteReadError, ConnectionResetError):
                    break

                try:
                    req = decode_request(raw)
                except Exception as exc:
                    logger.warning("Failed to decode request: %s", exc)
                    break

                response = await self._dispatch(req)
                writer.write(encode(response))
                await writer.drain()
        finally:
            writer.close()

    async def _dispatch(self, req: DaemonRequest) -> DaemonResponse:
        """Route a request to the appropriate handler method."""
        handlers = {
            Method.PS: self._handle_ps,
            Method.INSPECT: self._handle_inspect,
            Method.STOP: self._handle_stop,
            Method.RM: self._handle_rm,
            Method.INFO: self._handle_info,
        }
        handler = handlers.get(req.method)
        if handler is None:
            return DaemonResponse(id=req.id, error=f"Method not implemented: {req.method}")

        try:
            result = await handler(req.params)
            return DaemonResponse(id=req.id, result=result)
        except Exception as exc:
            logger.exception("Handler error for %s: %s", req.method, exc)
            return DaemonResponse(id=req.id, error=str(exc))

    async def _handle_ps(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        all_containers = params.get("all", False)
        return self._manager.list_containers(all_states=all_containers)

    async def _handle_inspect(self, params: dict[str, Any]) -> dict[str, Any]:
        container_id = params["container_id"]
        return self._manager._state.get(container_id)

    async def _handle_stop(self, params: dict[str, Any]) -> dict[str, Any]:
        container_id = params["container_id"]
        timeout = params.get("timeout", 10)
        self._manager.stop(container_id, timeout=timeout)
        return {"status": "stopped"}

    async def _handle_rm(self, params: dict[str, Any]) -> dict[str, Any]:
        container_id = params["container_id"]
        force = params.get("force", False)
        if force:
            try:
                self._manager.stop(container_id)
            except Exception:
                pass
        self._manager.remove(container_id)
        return {"status": "removed"}

    async def _handle_info(self, params: dict[str, Any]) -> dict[str, Any]:
        import platform
        from pybox import __version__
        return {
            "version": __version__,
            "root": str(self._cfg.root),
            "cgroup_root": str(self._cfg.cgroup_root),
            "kernel": platform.release(),
            "rootless": os.getuid() != 0,
        }


def main() -> None:
    """Entry point for pyboxd (registered in pyproject.toml scripts)."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    daemon = PyBoxDaemon()
    loop = asyncio.new_event_loop()

    def _shutdown(sig: signal.Signals) -> None:
        logger.info("Received %s, shutting down...", sig.name)
        loop.stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _shutdown, sig)

    try:
        loop.run_until_complete(daemon.start())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()
        DEFAULT_SOCKET.unlink(missing_ok=True)
        logger.info("pyboxd stopped")
