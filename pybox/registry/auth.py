"""Docker Hub Bearer token authentication and credential storage.

Implements the Docker Registry v2 auth flow:
    1. Request token from auth.docker.io with repository scope
    2. Cache the token in memory until it expires
    3. Support optional username/password for private repositories

Credential persistence:
    Credentials are stored in <PYBOX_ROOT>/config/auth.json (chmod 600).
    The DOCKER_CONFIG env var can override the storage location.

Reference: https://docs.docker.com/registry/spec/auth/token/
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx

from pybox.exceptions import RegistryError

logger = logging.getLogger(__name__)

_AUTH_URL = "https://auth.docker.io/token"
_AUTH_SERVICE = "registry.docker.io"


class DockerHubAuth:
    """Manages Bearer token authentication for Docker Hub.

    Tokens are cached in memory keyed by repository scope. A token is
    considered expired when fewer than 30 seconds remain on its TTL.

    Usage:
        auth = DockerHubAuth()
        token = await auth.get_token("library/ubuntu", ["pull"])
    """

    def __init__(
        self,
        auth_url: str = _AUTH_URL,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self._auth_url = auth_url
        self._username = username
        self._password = password
        # Cache: scope → (token_str, expiry_timestamp)
        self._cache: dict[str, tuple[str, float]] = {}

    def _cache_key(self, repository: str, actions: list[str]) -> str:
        return f"repository:{repository}:{','.join(sorted(actions))}"

    def _is_valid(self, expiry: float) -> bool:
        return time.monotonic() < expiry - 30

    async def get_token(
        self,
        repository: str,
        actions: list[str],
        registry: str = "registry-1.docker.io",
    ) -> str:
        """Obtain a Bearer token for the given repository and actions.

        Returns a cached token if still valid, otherwise fetches a fresh one.

        Args:
            repository: OCI repository name, e.g. "library/ubuntu".
            actions:    List of requested actions, e.g. ["pull"] or ["pull", "push"].
            registry:   Registry host (used to pick the auth service name).

        Returns:
            Bearer token string.

        Raises:
            RegistryError: If the auth endpoint returns an error.
        """
        key = self._cache_key(repository, actions)
        if key in self._cache:
            token, expiry = self._cache[key]
            if self._is_valid(expiry):
                logger.debug("Using cached auth token for %s", repository)
                return token

        token, expiry = await self._fetch_token(repository, actions, registry)
        self._cache[key] = (token, expiry)
        logger.debug(
            "Fetched new auth token for %s (expires in %.0fs)",
            repository,
            expiry - time.monotonic(),
        )
        return token

    async def _fetch_token(
        self,
        repository: str,
        actions: list[str],
        registry: str,
    ) -> tuple[str, float]:
        """Fetch a fresh token from the auth endpoint."""
        scope = f"repository:{repository}:{','.join(actions)}"
        service = _AUTH_SERVICE if "docker.io" in registry else registry
        params: dict[str, str] = {"service": service, "scope": scope}
        auth: tuple[str, str] | None = None
        if self._username and self._password:
            auth = (self._username, self._password)

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(self._auth_url, params=params, auth=auth)

        if resp.status_code != 200:
            raise RegistryError(
                f"Auth failed for repository '{repository}'",
                status_code=resp.status_code,
            )

        data: dict[str, Any] = resp.json()
        token: str = data.get("token") or data.get("access_token", "")
        if not token:
            raise RegistryError("Registry auth endpoint returned an empty token")

        expires_in: int = int(data.get("expires_in", 300))
        return token, time.monotonic() + expires_in

    def invalidate(self, repository: str, actions: list[str]) -> None:
        """Remove a cached token, forcing a fresh fetch on next use."""
        self._cache.pop(self._cache_key(repository, actions), None)

    # ------------------------------------------------------------------
    # Credential persistence
    # ------------------------------------------------------------------

    @staticmethod
    def _auth_file(config_dir: Path | None = None) -> Path:
        """Return the path to the auth.json credential store."""
        if config_dir is None:
            docker_config = os.environ.get("DOCKER_CONFIG")
            if docker_config:
                config_dir = Path(docker_config)
            else:
                from pybox.config import get_config
                config_dir = get_config().root / "config"
        return config_dir / "auth.json"

    @classmethod
    def save_credentials(
        cls,
        registry: str,
        username: str,
        token: str,
        config_dir: Path | None = None,
    ) -> None:
        """Save credentials to auth.json with 0o600 permissions.

        Args:
            registry:   Registry hostname, e.g. "registry-1.docker.io".
            username:   Username or token ID.
            token:      Password, personal access token, or refresh token.
            config_dir: Override for the config directory.
        """
        auth_file = cls._auth_file(config_dir)
        auth_file.parent.mkdir(parents=True, exist_ok=True)

        if auth_file.exists():
            data: dict[str, Any] = json.loads(auth_file.read_text())
        else:
            data = {"auths": {}}

        data.setdefault("auths", {})[registry] = {
            "username": username,
            "auth": token,
        }

        auth_file.write_text(json.dumps(data, indent=2))
        # Restrict permissions: credentials file must not be world-readable
        auth_file.chmod(0o600)
        logger.debug("Saved credentials for %s to %s", registry, auth_file)

    @classmethod
    def load_credentials(
        cls,
        registry: str,
        config_dir: Path | None = None,
    ) -> tuple[str, str] | None:
        """Load credentials for a registry from auth.json.

        Args:
            registry:   Registry hostname.
            config_dir: Override for the config directory.

        Returns:
            (username, token) tuple or None if not found.
        """
        auth_file = cls._auth_file(config_dir)
        if not auth_file.exists():
            return None
        try:
            data = json.loads(auth_file.read_text())
            entry = data.get("auths", {}).get(registry, {})
            username = entry.get("username", "")
            token = entry.get("auth", "")
            if username and token:
                return username, token
        except (json.JSONDecodeError, OSError):
            pass
        return None
