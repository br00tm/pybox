"""Container log capture and streaming.

Captures container stdout/stderr to timestamped log files and provides
a streaming interface for `pybox logs --follow`.

Log files are stored at:
    <PYBOX_ROOT>/containers/<id>/logs/stdout.log
    <PYBOX_ROOT>/containers/<id>/logs/stderr.log

Each line is prefixed with an ISO 8601 timestamp:
    2026-01-15T10:23:45.123Z [stdout] Hello, world!
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

logger = logging.getLogger(__name__)


class LogManager:
    """Captures and streams container log output.

    Args:
        containers_dir: Root of container storage.
    """

    def __init__(self, containers_dir: Path) -> None:
        self._containers_dir = containers_dir

    def _log_dir(self, container_id: str) -> Path:
        return self._containers_dir / container_id / "logs"

    def _log_file(self, container_id: str, stream: str) -> Path:
        return self._log_dir(container_id) / f"{stream}.log"

    def start_capture(
        self,
        container_id: str,
        stdout_fd: int,
        stderr_fd: int,
    ) -> list[asyncio.Task]:
        """Start asyncio tasks to capture container stdout/stderr.

        Each task reads from the file descriptor and appends timestamped
        lines to the log file.

        Args:
            container_id: Container being captured.
            stdout_fd:    File descriptor for container stdout.
            stderr_fd:    File descriptor for container stderr.

        Returns:
            List of asyncio.Task objects (one per stream).
        """
        log_dir = self._log_dir(container_id)
        log_dir.mkdir(parents=True, exist_ok=True)

        tasks = []
        for fd, stream_name in [(stdout_fd, "stdout"), (stderr_fd, "stderr")]:
            log_file = self._log_file(container_id, stream_name)
            task = asyncio.ensure_future(
                self._capture_stream(fd, log_file, stream_name)
            )
            tasks.append(task)

        return tasks

    async def _capture_stream(
        self, fd: int, log_file: Path, stream_name: str
    ) -> None:
        """Read from fd and write timestamped lines to log_file."""
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        transport, _ = await loop.connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), os.fdopen(fd, "rb")
        )

        with open(log_file, "a") as f:
            try:
                while True:
                    line = await reader.readline()
                    if not line:
                        break
                    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
                    f.write(f"{ts} [{stream_name}] {line.decode(errors='replace')}")
                    f.flush()
            except asyncio.CancelledError:
                pass
            finally:
                transport.close()

    async def stream(
        self,
        container_id: str,
        follow: bool = False,
        tail: int = 100,
    ) -> AsyncIterator[str]:
        """Stream log lines for a container.

        Args:
            container_id: Container ID.
            follow:       If True, keep reading new lines as they arrive.
            tail:         Number of historical lines to return first.

        Yields:
            Log lines as strings (with timestamp prefix).
        """
        log_file = self._log_file(container_id, "stdout")
        err_file = self._log_file(container_id, "stderr")

        # Merge stdout and stderr into chronological order (best-effort)
        lines: list[str] = []
        for f in [log_file, err_file]:
            if f.exists():
                lines.extend(f.read_text().splitlines(keepends=True))

        # Sort by timestamp prefix (ISO 8601 sorts lexicographically)
        lines.sort()

        # Emit tail lines
        for line in lines[-tail:]:
            yield line

        if not follow:
            return

        # Follow mode: poll for new content
        pos = log_file.stat().st_size if log_file.exists() else 0
        while True:
            await asyncio.sleep(0.1)
            if log_file.exists():
                current_size = log_file.stat().st_size
                if current_size > pos:
                    with open(log_file) as f:
                        f.seek(pos)
                        for line in f:
                            yield line
                    pos = current_size
