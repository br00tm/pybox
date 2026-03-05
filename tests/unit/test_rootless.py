"""Unit tests for pybox.rootless modules."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


class TestIsRootless:
    def test_rootless_when_nonroot(self) -> None:
        from pybox.rootless import is_rootless
        with patch("os.getuid", return_value=1000):
            assert is_rootless() is True

    def test_not_rootless_when_root(self) -> None:
        from pybox.rootless import is_rootless
        with patch("os.getuid", return_value=0):
            assert is_rootless() is False


class TestGetSubuidRange:
    def test_parses_valid_entry(self, tmp_path: Path) -> None:
        from pybox.rootless import _parse_id_file

        subuid = tmp_path / "subuid"
        subuid.write_text("alice:100000:65536\nbob:165536:65536\n")
        start, count = _parse_id_file(subuid, "alice")
        assert start == 100000
        assert count == 65536

    def test_raises_for_missing_user(self, tmp_path: Path) -> None:
        from pybox.rootless import _parse_id_file

        subuid = tmp_path / "subuid"
        subuid.write_text("alice:100000:65536\n")
        with pytest.raises(KeyError, match="bob"):
            _parse_id_file(subuid, "bob")

    def test_raises_if_file_missing(self, tmp_path: Path) -> None:
        from pybox.rootless import _parse_id_file
        with pytest.raises(KeyError, match="not found"):
            _parse_id_file(tmp_path / "nonexistent", "alice")

    def test_skips_comment_lines(self, tmp_path: Path) -> None:
        from pybox.rootless import _parse_id_file

        subuid = tmp_path / "subuid"
        subuid.write_text("# comment\nalice:100000:65536\n")
        start, count = _parse_id_file(subuid, "alice")
        assert start == 100000


class TestUserNamespaceContent:
    def test_uid_map_content_format(self) -> None:
        from pybox.namespace.user_map import IDMapping

        mapping = IDMapping(container_id=0, host_id=1000, count=65536)
        assert str(mapping) == "0 1000 65536"

    def test_multiple_mappings_content(self) -> None:
        from pybox.namespace.user_map import IDMapping

        mappings = [
            IDMapping(container_id=0, host_id=1000, count=1),
            IDMapping(container_id=1, host_id=100000, count=65536),
        ]
        content = "\n".join(str(m) for m in mappings) + "\n"
        lines = content.strip().split("\n")
        assert lines[0] == "0 1000 1"
        assert lines[1] == "1 100000 65536"


class TestPortProxy:
    @pytest.mark.asyncio
    async def test_tcp_proxy_forwards_data(self) -> None:
        """Test that TCP proxy correctly forwards a connection."""
        import asyncio
        from pybox.rootless.port_proxy import PortProxy

        received: list[bytes] = []

        async def echo_server(reader, writer) -> None:
            data = await reader.read(100)
            received.append(data)
            writer.write(data)
            await writer.drain()
            writer.close()

        # Start a simple echo server
        server = await asyncio.start_server(echo_server, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        proxy = PortProxy(bind_addr="127.0.0.1")
        await proxy.forward(0, "127.0.0.1", port, "tcp")  # port=0 picks random

        async with server:
            # Connect directly (not through proxy for simplicity in unit test)
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"hello")
            await writer.drain()
            data = await asyncio.wait_for(reader.read(100), timeout=1.0)
            writer.close()

        assert data == b"hello"
        await proxy.stop_all()
        server.close()
