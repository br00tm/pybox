"""Unit tests for pybox.container.logs.LogManager."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


class TestLogManager:
    def _make_log_dir(self, tmp_path: Path, container_id: str = "abc123") -> Path:
        log_dir = tmp_path / container_id / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir

    def _write_log(self, tmp_path: Path, container_id: str, stream: str, lines: list[str]) -> None:
        log_dir = self._make_log_dir(tmp_path, container_id)
        content = ""
        for i, line in enumerate(lines):
            content += f"2026-01-15T10:00:{i:02d}.000Z [{stream}] {line}\n"
        (log_dir / f"{stream}.log").write_text(content)

    def test_stream_returns_tail_lines(self, tmp_path: Path) -> None:
        from pybox.container.logs import LogManager

        cid = "abc123"
        self._write_log(tmp_path, cid, "stdout", [f"line {i}" for i in range(20)])
        mgr = LogManager(tmp_path)

        async def _collect():
            return [line async for line in mgr.stream(cid, follow=False, tail=5)]

        lines = asyncio.run(_collect())
        assert len(lines) == 5
        assert "line 15" in lines[-1] or "line 19" in lines[-1]

    def test_stream_all_when_tail_exceeds_total(self, tmp_path: Path) -> None:
        from pybox.container.logs import LogManager

        cid = "def456"
        self._write_log(tmp_path, cid, "stdout", ["a", "b", "c"])
        mgr = LogManager(tmp_path)

        async def _collect():
            return [line async for line in mgr.stream(cid, follow=False, tail=100)]

        lines = asyncio.run(_collect())
        assert len(lines) == 3

    def test_stream_empty_when_no_logs(self, tmp_path: Path) -> None:
        from pybox.container.logs import LogManager

        cid = "empty123"
        (tmp_path / cid / "logs").mkdir(parents=True, exist_ok=True)
        mgr = LogManager(tmp_path)

        async def _collect():
            return [line async for line in mgr.stream(cid, follow=False, tail=100)]

        lines = asyncio.run(_collect())
        assert lines == []

    def test_stream_follow_false_does_not_block(self, tmp_path: Path) -> None:
        from pybox.container.logs import LogManager

        cid = "follow123"
        self._write_log(tmp_path, cid, "stdout", ["hello"])
        mgr = LogManager(tmp_path)

        async def _timed():
            lines = []
            async for line in mgr.stream(cid, follow=False, tail=100):
                lines.append(line)
            return lines

        # Should complete quickly (no blocking)
        lines = asyncio.run(asyncio.wait_for(_timed(), timeout=2.0))
        assert len(lines) == 1

    def test_merges_stdout_and_stderr(self, tmp_path: Path) -> None:
        from pybox.container.logs import LogManager

        cid = "merge123"
        self._write_log(tmp_path, cid, "stdout", ["out line"])
        self._write_log(tmp_path, cid, "stderr", ["err line"])
        mgr = LogManager(tmp_path)

        async def _collect():
            return [line async for line in mgr.stream(cid, follow=False, tail=100)]

        lines = asyncio.run(_collect())
        # Both streams should be present
        combined = " ".join(lines)
        assert "out line" in combined
        assert "err line" in combined
