"""Container state management: FSM and persistent JSON storage.

States:
    CREATED  — container created but not started
    RUNNING  — container process is alive
    PAUSED   — container is suspended (SIGSTOP)
    STOPPED  — container has exited
    REMOVED  — container resources have been cleaned up

State is persisted to:
    <PYBOX_ROOT>/containers/<id>/state.json
"""

from __future__ import annotations

import json
import logging
from enum import Enum
from pathlib import Path
from typing import Any

from pybox.exceptions import ContainerNotFoundError

logger = logging.getLogger(__name__)


class ContainerState(str, Enum):
    """Valid lifecycle states for a PyBox container."""

    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    REMOVED = "removed"


class StateManager:
    """Persists and retrieves container state as JSON files.

    Each container's state file lives at:
        <containers_dir>/<container_id>/state.json

    The file contains a flat JSON object with at least:
        {"id": "...", "state": "running", "pid": 12345, ...}
    """

    def __init__(self, containers_dir: Path) -> None:
        self._containers_dir = containers_dir

    def _state_file(self, container_id: str) -> Path:
        return self._containers_dir / container_id / "state.json"

    def _container_dir(self, container_id: str) -> Path:
        return self._containers_dir / container_id

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(self, container_id: str) -> dict[str, Any]:
        """Load and return the full state dict for a container.

        Args:
            container_id: Container ID (12-char hex).

        Returns:
            State dict including at least {"id", "state"}.

        Raises:
            ContainerNotFoundError: If the state file does not exist.
        """
        path = self._state_file(container_id)
        if not path.exists():
            raise ContainerNotFoundError(container_id)
        data: dict[str, Any] = json.loads(path.read_text())
        return data

    def list_all(self) -> list[dict[str, Any]]:
        """Return state dicts for all known containers.

        Containers whose state files cannot be read are silently skipped.

        Returns:
            List of state dicts, one per container.
        """
        results: list[dict[str, Any]] = []
        if not self._containers_dir.exists():
            return results
        for container_dir in self._containers_dir.iterdir():
            if not container_dir.is_dir():
                continue
            state_file = container_dir / "state.json"
            if not state_file.exists():
                continue
            try:
                results.append(json.loads(state_file.read_text()))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to read state for %s: %s", container_dir.name, exc)
        return results

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def create(self, container_id: str, initial: dict[str, Any]) -> None:
        """Write the initial state file for a new container.

        Args:
            container_id: Container ID.
            initial:      Initial state dict (must include "state" key).
        """
        container_dir = self._container_dir(container_id)
        container_dir.mkdir(parents=True, exist_ok=True)
        path = self._state_file(container_id)
        # Ensure the container_id is always in the file
        data: dict[str, Any] = {"id": container_id, **initial}
        path.write_text(json.dumps(data, indent=2))
        logger.debug("Created state for container %s: %s", container_id, data.get("state"))

    def update(self, container_id: str, **fields: Any) -> None:
        """Merge new fields into an existing state file.

        Args:
            container_id: Container ID.
            **fields:     Key-value pairs to merge into the state dict.

        Raises:
            ContainerNotFoundError: If the container does not exist.
        """
        data = self.get(container_id)
        data.update(fields)
        self._state_file(container_id).write_text(json.dumps(data, indent=2))
        logger.debug(
            "Updated state for container %s: %s",
            container_id,
            {k: v for k, v in fields.items()},
        )

    def set_state(self, container_id: str, state: ContainerState) -> None:
        """Update only the "state" field in the container's state file.

        Args:
            container_id: Container ID.
            state:        New ContainerState value.
        """
        self.update(container_id, state=state.value)

    def remove(self, container_id: str) -> None:
        """Remove the container's state directory entirely.

        Args:
            container_id: Container ID.
        """
        import shutil

        container_dir = self._container_dir(container_id)
        if container_dir.exists():
            shutil.rmtree(container_dir)
            logger.debug("Removed state directory for container %s", container_id)
