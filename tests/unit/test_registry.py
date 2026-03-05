"""Unit tests for pybox.registry modules.

All HTTP interactions are mocked — no real network access required.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pybox.exceptions import ImageNotFoundError, RegistryError


# ------------------------------------------------------------------
# _parse_image_ref
# ------------------------------------------------------------------

class TestParseImageRef:
    def test_official_image_no_tag(self) -> None:
        from pybox.registry.client import _parse_image_ref
        registry, repo, tag = _parse_image_ref("ubuntu")
        assert registry == "registry-1.docker.io"
        assert repo == "library/ubuntu"
        assert tag == "latest"

    def test_official_image_with_tag(self) -> None:
        from pybox.registry.client import _parse_image_ref
        registry, repo, tag = _parse_image_ref("ubuntu:24.04")
        assert registry == "registry-1.docker.io"
        assert repo == "library/ubuntu"
        assert tag == "24.04"

    def test_user_image(self) -> None:
        from pybox.registry.client import _parse_image_ref
        registry, repo, tag = _parse_image_ref("myuser/myapp:v1")
        assert registry == "registry-1.docker.io"
        assert repo == "myuser/myapp"
        assert tag == "v1"

    def test_explicit_registry(self) -> None:
        from pybox.registry.client import _parse_image_ref
        registry, repo, tag = _parse_image_ref("ghcr.io/org/app:latest")
        assert registry == "ghcr.io"
        assert repo == "org/app"
        assert tag == "latest"

    def test_localhost_registry(self) -> None:
        from pybox.registry.client import _parse_image_ref
        registry, repo, tag = _parse_image_ref("localhost:5000/myimage:dev")
        assert registry == "localhost:5000"
        assert repo == "myimage"
        assert tag == "dev"


# ------------------------------------------------------------------
# DockerHubAuth
# ------------------------------------------------------------------

class TestDockerHubAuth:
    @pytest.mark.asyncio
    async def test_get_token_fetches_and_caches(self) -> None:
        from pybox.registry.auth import DockerHubAuth

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"token": "mytoken123", "expires_in": 300}

        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.get = AsyncMock(return_value=mock_response)

            auth = DockerHubAuth()
            token = await auth.get_token("library/ubuntu", ["pull"])
            assert token == "mytoken123"

            # Second call should use cache (no additional HTTP request)
            token2 = await auth.get_token("library/ubuntu", ["pull"])
            assert token2 == "mytoken123"
            assert instance.get.call_count == 1  # Only called once

    @pytest.mark.asyncio
    async def test_get_token_raises_on_auth_failure(self) -> None:
        from pybox.registry.auth import DockerHubAuth

        mock_response = MagicMock()
        mock_response.status_code = 401

        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.get = AsyncMock(return_value=mock_response)

            auth = DockerHubAuth()
            with pytest.raises(RegistryError, match="Auth failed"):
                await auth.get_token("library/ubuntu", ["pull"])

    @pytest.mark.asyncio
    async def test_expired_token_is_refreshed(self) -> None:
        from pybox.registry.auth import DockerHubAuth

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"token": "newtoken", "expires_in": 1}

        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.get = AsyncMock(return_value=mock_response)

            auth = DockerHubAuth()
            # Manually inject an expired token into the cache
            auth._cache["repository:library/ubuntu:pull"] = ("oldtoken", time.monotonic() - 1)

            token = await auth.get_token("library/ubuntu", ["pull"])
            # Should have fetched a new token
            assert token == "newtoken"
            instance.get.assert_called_once()


# ------------------------------------------------------------------
# RegistryClient — manifest and layer
# ------------------------------------------------------------------

class TestRegistryClient:
    def _make_manifest(self) -> dict:
        return {
            "schemaVersion": 2,
            "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
            "config": {
                "mediaType": "application/vnd.docker.container.image.v1+json",
                "digest": "sha256:abc123",
                "size": 100,
            },
            "layers": [
                {
                    "mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip",
                    "digest": "sha256:layer1",
                    "size": 1000,
                }
            ],
        }

    @pytest.mark.asyncio
    async def test_get_manifest_returns_parsed_json(self) -> None:
        from pybox.registry.client import RegistryClient

        manifest_data = self._make_manifest()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = manifest_data
        mock_resp.headers = {"content-type": "application/vnd.docker.distribution.manifest.v2+json"}

        client = RegistryClient()
        client._client = MagicMock()

        with patch.object(client, "_authed_request", new=AsyncMock(return_value=mock_resp)):
            result = await client.get_manifest("ubuntu:24.04")

        assert result["schemaVersion"] == 2
        assert len(result["layers"]) == 1

    @pytest.mark.asyncio
    async def test_get_manifest_raises_image_not_found(self) -> None:
        from pybox.registry.client import RegistryClient

        mock_resp = MagicMock()
        mock_resp.status_code = 404

        client = RegistryClient()
        client._client = MagicMock()

        with patch.object(client, "_authed_request", new=AsyncMock(return_value=mock_resp)):
            with pytest.raises(ImageNotFoundError, match="ubuntu:nonexistent"):
                await client.get_manifest("ubuntu:nonexistent")

    @pytest.mark.asyncio
    async def test_pull_layer_verifies_sha256(self, tmp_path: Path) -> None:
        from pybox.registry.client import RegistryClient

        # Create fake layer content and compute its digest
        content = b"fake layer content"
        digest_hex = hashlib.sha256(content).hexdigest()
        digest = f"sha256:{digest_hex}"

        # Mock streaming response
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        async def fake_aiter_bytes(chunk_size: int = 65536):
            yield content

        mock_resp.aiter_bytes = fake_aiter_bytes
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        client = RegistryClient()
        mock_http_client = MagicMock()
        mock_http_client.stream.return_value = mock_resp
        client._client = mock_http_client

        with patch.object(client, "_get_token", new=AsyncMock(return_value="token")):
            layer_path = await client.pull_layer(
                "library/ubuntu", "registry-1.docker.io", digest, tmp_path
            )

        assert layer_path.exists()
        assert layer_path.read_bytes() == content

    @pytest.mark.asyncio
    async def test_pull_layer_raises_on_digest_mismatch(self, tmp_path: Path) -> None:
        from pybox.registry.client import RegistryClient

        content = b"tampered content"
        wrong_digest = "sha256:" + "0" * 64  # wrong digest

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        async def fake_aiter_bytes(chunk_size: int = 65536):
            yield content

        mock_resp.aiter_bytes = fake_aiter_bytes
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        client = RegistryClient()
        mock_http_client = MagicMock()
        mock_http_client.stream.return_value = mock_resp
        client._client = mock_http_client

        with patch.object(client, "_get_token", new=AsyncMock(return_value="token")):
            with pytest.raises(RegistryError, match="digest mismatch"):
                await client.pull_layer(
                    "library/ubuntu", "registry-1.docker.io", wrong_digest, tmp_path
                )

    @pytest.mark.asyncio
    async def test_pull_layer_skips_if_cached(self, tmp_path: Path) -> None:
        from pybox.registry.client import RegistryClient

        digest = "sha256:" + "a" * 64
        cached_file = tmp_path / f"{digest.replace(':', '_')}.tar.gz"
        cached_file.write_bytes(b"cached layer")

        client = RegistryClient()
        client._client = MagicMock()

        # _get_token should NOT be called if file is already cached
        with patch.object(client, "_get_token", new=AsyncMock()) as mock_token:
            result = await client.pull_layer(
                "library/ubuntu", "registry-1.docker.io", digest, tmp_path
            )

        assert result == cached_file
        mock_token.assert_not_called()
