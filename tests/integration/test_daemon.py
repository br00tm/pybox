"""Integration tests for pyboxd daemon.

These tests start a real pyboxd on a temporary socket path and communicate
with it via DaemonClient. ContainerManager is mocked to avoid real container
operations.

Requires: pytest-asyncio
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pybox.daemon.protocol import Method


@pytest.mark.asyncio
async def test_daemon_ps_returns_empty_list(tmp_path: Path) -> None:
    """pyboxd responds to PS with an empty list when no containers exist."""
    from pybox.daemon.main import PyBoxDaemon
    from pybox.daemon.client import DaemonClient

    socket_path = tmp_path / "test.sock"

    # Patch the container manager to avoid filesystem access
    mock_manager = MagicMock()
    mock_manager.list_containers.return_value = []
    mock_manager._state.list_all.return_value = []
    mock_manager._state.get.side_effect = Exception("not found")

    daemon = PyBoxDaemon(socket_path=socket_path)
    daemon._manager = mock_manager

    # Patch crash recovery to be a no-op
    daemon._recover_state = MagicMock()

    # Start daemon in background
    server_task = asyncio.create_task(daemon.start())
    await asyncio.sleep(0.2)  # Let server start

    try:
        client = DaemonClient(socket_path=socket_path)
        await client.connect()
        result = await client.call(Method.PS)
        await client.disconnect()

        assert isinstance(result, list)
        assert result == []
    finally:
        server_task.cancel()
        try:
            await server_task
        except (asyncio.CancelledError, Exception):
            pass


@pytest.mark.asyncio
async def test_daemon_info_returns_version(tmp_path: Path) -> None:
    """pyboxd INFO method returns version string."""
    from pybox.daemon.main import PyBoxDaemon
    from pybox.daemon.client import DaemonClient
    from pybox import __version__

    socket_path = tmp_path / "test2.sock"
    daemon = PyBoxDaemon(socket_path=socket_path)
    daemon._recover_state = MagicMock()

    server_task = asyncio.create_task(daemon.start())
    await asyncio.sleep(0.2)

    try:
        client = DaemonClient(socket_path=socket_path)
        await client.connect()
        result = await client.call(Method.INFO)
        await client.disconnect()

        assert result["version"] == __version__
        assert "rootless" in result
        assert "kernel" in result
    finally:
        server_task.cancel()
        try:
            await server_task
        except (asyncio.CancelledError, Exception):
            pass
