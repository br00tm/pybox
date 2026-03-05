"""Image tag management.

Maps human-readable tags (e.g. "myapp:v1") to content digests
(e.g. "sha256:abc123..."). Persisted to:
    <PYBOX_ROOT>/images/tags.json
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pybox.exceptions import ImageNotFoundError

logger = logging.getLogger(__name__)


class TagManager:
    """Manages the tag → digest index for local images.

    Args:
        images_dir: Root of the local image store.
    """

    def __init__(self, images_dir: Path) -> None:
        self._images_dir = images_dir
        self._tags_file = images_dir / "tags.json"

    def _load(self) -> dict[str, str]:
        if not self._tags_file.exists():
            return {}
        try:
            return json.loads(self._tags_file.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self, tags: dict[str, str]) -> None:
        self._images_dir.mkdir(parents=True, exist_ok=True)
        self._tags_file.write_text(json.dumps(tags, indent=2))

    def tag(self, source_ref: str, target_ref: str) -> None:
        """Create a new tag pointing to the same digest as source_ref.

        Args:
            source_ref: Existing tag or digest.
            target_ref: New tag to create.

        Raises:
            ImageNotFoundError: If source_ref doesn't exist.
        """
        tags = self._load()
        digest = tags.get(source_ref) or source_ref  # allow tagging by digest directly
        if not digest.startswith("sha256:"):
            raise ImageNotFoundError(source_ref)
        tags[target_ref] = digest
        self._save(tags)
        logger.debug("Tagged %s → %s (%s)", source_ref, target_ref, digest[:20])

    def resolve(self, ref: str) -> str:
        """Resolve a tag to its content digest.

        Args:
            ref: Tag string or full digest.

        Returns:
            Full digest string, e.g. "sha256:abc123...".

        Raises:
            ImageNotFoundError: If the tag is not found.
        """
        if ref.startswith("sha256:"):
            return ref
        tags = self._load()
        if ref not in tags:
            raise ImageNotFoundError(ref)
        return tags[ref]

    def untag(self, ref: str) -> None:
        """Remove a tag.

        Args:
            ref: Tag to remove.

        Raises:
            ImageNotFoundError: If the tag doesn't exist.
        """
        tags = self._load()
        if ref not in tags:
            raise ImageNotFoundError(ref)
        del tags[ref]
        self._save(tags)
        logger.debug("Untagged: %s", ref)

    def list_tags(self) -> list[dict[str, str]]:
        """Return all tags as a list of {tag, digest} dicts."""
        tags = self._load()
        return [{"tag": k, "digest": v} for k, v in sorted(tags.items())]
