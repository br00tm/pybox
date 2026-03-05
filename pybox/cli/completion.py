"""Shell completion helpers for PyBox CLI.

Provides autocompletion functions that typer uses to suggest container IDs
and names when the user presses Tab.

Usage in typer commands:
    Annotated[str, typer.Argument(autocompletion=complete_container)]
"""

from __future__ import annotations

from typing import Any


def _running_containers() -> list[dict[str, Any]]:
    """Return running containers from local state, silently on error."""
    try:
        from pybox.config import get_config
        from pybox.container.runtime import ContainerManager
        return ContainerManager(get_config()).list_containers(all_states=False)
    except Exception:
        return []


def _all_containers() -> list[dict[str, Any]]:
    """Return all containers (any state) from local state, silently on error."""
    try:
        from pybox.config import get_config
        from pybox.container.runtime import ContainerManager
        return ContainerManager(get_config()).list_containers(all_states=True)
    except Exception:
        return []


def complete_container(ctx: Any, args: Any, incomplete: str) -> list[str]:
    """Suggest container IDs and names for shell completion.

    Matches running containers whose ID starts with `incomplete` or whose
    name contains `incomplete`.
    """
    suggestions: list[str] = []
    for c in _all_containers():
        cid = c.get("id", "")
        name = c.get("name") or ""
        if cid.startswith(incomplete) and cid not in suggestions:
            suggestions.append(cid[:12])
        if name and name.startswith(incomplete) and name not in suggestions:
            suggestions.append(name)
    return suggestions


def complete_running_container(ctx: Any, args: Any, incomplete: str) -> list[str]:
    """Suggest only RUNNING container IDs/names for shell completion."""
    suggestions: list[str] = []
    for c in _running_containers():
        cid = c.get("id", "")
        name = c.get("name") or ""
        if cid.startswith(incomplete):
            suggestions.append(cid[:12])
        if name and name.startswith(incomplete):
            suggestions.append(name)
    return suggestions


def complete_image(ctx: Any, args: Any, incomplete: str) -> list[str]:
    """Suggest locally cached image names for shell completion."""
    try:
        from pybox.config import get_config
        images_dir = get_config().images_dir
        if not images_dir.exists():
            return []
        return [
            d.name for d in images_dir.iterdir()
            if d.is_dir() and d.name.startswith(incomplete)
        ]
    except Exception:
        return []
