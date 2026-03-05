"""Unit tests for pybox.registry.push."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pybox.image.manifest import OciManifest
from pybox.registry.auth import DockerHubAuth


def _make_manifest() -> OciManifest:
    """Create a minimal OciManifest for testing."""
    return OciManifest(
        **{
            "schemaVersion": 2,
            "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
            "config": {
                "mediaType": "application/vnd.docker.container.image.v1+json",
                "digest": "sha256:" + "a" * 64,
                "size": 100,
            },
            "layers": [
                {
                    "mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip",
                    "digest": "sha256:" + "b" * 64,
                    "size": 1000,
                }
            ],
        }
    )


class TestOciRegistryPusher:
    @pytest.mark.asyncio
    async def test_skips_existing_layer(self, tmp_path: Path) -> None:
        from pybox.registry.push import OciRegistryPusher

        manifest = _make_manifest()
        layer_digest = manifest.layers[0].digest
        safe_name = layer_digest.replace(":", "_") + ".tar.gz"
        layer_file = tmp_path / "layers" / safe_name
        layer_file.parent.mkdir(parents=True, exist_ok=True)
        layer_file.write_bytes(b"fake layer")

        auth = DockerHubAuth()
        pusher = OciRegistryPusher(auth)
        pusher._client = MagicMock()

        # HEAD response: 200 = blob already exists
        head_resp = MagicMock()
        head_resp.status_code = 200
        pusher._client.head = AsyncMock(return_value=head_resp)

        await pusher._push_blob(
            "library/ubuntu", "registry-1.docker.io", layer_digest, tmp_path / "layers", "token"
        )

        # HEAD was called to check existence
        pusher._client.head.assert_called_once()

    @pytest.mark.asyncio
    async def test_uploads_missing_layer(self, tmp_path: Path) -> None:
        from pybox.registry.push import OciRegistryPusher

        content = b"new layer content"
        digest_hex = hashlib.sha256(content).hexdigest()
        digest = f"sha256:{digest_hex}"
        layer_file = tmp_path / f"{digest.replace(':', '_')}.tar.gz"
        layer_file.write_bytes(content)

        auth = DockerHubAuth()
        pusher = OciRegistryPusher(auth)

        # Mock: HEAD → 404 (blob missing), POST → 202, PUT → 201
        head_resp = MagicMock(status_code=404)
        post_resp = MagicMock(status_code=202)
        post_resp.headers = {"Location": "/v2/repo/blobs/uploads/some-uuid"}
        put_resp = MagicMock(status_code=201)

        pusher._client = MagicMock()
        pusher._client.head = AsyncMock(return_value=head_resp)
        pusher._client.post = AsyncMock(return_value=post_resp)
        pusher._client.put = AsyncMock(return_value=put_resp)

        with patch.object(pusher, "_get_token" if hasattr(pusher, "_get_token") else "_auth", create=True):
            await pusher._push_blob(
                "library/ubuntu", "registry-1.docker.io", digest, tmp_path, "token"
            )

        pusher._client.post.assert_called_once()
        pusher._client.put.assert_called_once()

    @pytest.mark.asyncio
    async def test_push_manifest_uses_correct_content_type(self, tmp_path: Path) -> None:
        from pybox.registry.push import OciRegistryPusher
        from pybox.image.manifest import MEDIA_TYPE_MANIFEST_V2

        manifest = _make_manifest()
        auth = DockerHubAuth()
        pusher = OciRegistryPusher(auth)

        put_resp = MagicMock(status_code=201)
        put_resp.headers = {"Docker-Content-Digest": "sha256:abc"}
        pusher._client = MagicMock()
        pusher._client.put = AsyncMock(return_value=put_resp)

        await pusher._push_manifest(
            "myuser/myapp", "registry-1.docker.io", "v1", manifest, "token"
        )

        call_kwargs = pusher._client.put.call_args[1]
        assert call_kwargs["headers"]["Content-Type"] == MEDIA_TYPE_MANIFEST_V2


class TestDockerHubAuthCredentials:
    def test_save_credentials_creates_file_with_600_perms(self, tmp_path: Path) -> None:
        import stat
        DockerHubAuth.save_credentials(
            "registry-1.docker.io", "myuser", "mytoken", config_dir=tmp_path
        )
        auth_file = tmp_path / "auth.json"
        assert auth_file.exists()
        file_mode = stat.S_IMODE(auth_file.stat().st_mode)
        assert file_mode == 0o600

    def test_save_and_load_credentials_roundtrip(self, tmp_path: Path) -> None:
        DockerHubAuth.save_credentials(
            "registry-1.docker.io", "alice", "secret123", config_dir=tmp_path
        )
        result = DockerHubAuth.load_credentials("registry-1.docker.io", config_dir=tmp_path)
        assert result is not None
        username, token = result
        assert username == "alice"
        assert token == "secret123"

    def test_load_credentials_missing_file_returns_none(self, tmp_path: Path) -> None:
        result = DockerHubAuth.load_credentials("registry-1.docker.io", config_dir=tmp_path)
        assert result is None

    def test_save_credentials_for_multiple_registries(self, tmp_path: Path) -> None:
        DockerHubAuth.save_credentials("docker.io", "user1", "token1", config_dir=tmp_path)
        DockerHubAuth.save_credentials("ghcr.io", "user2", "token2", config_dir=tmp_path)

        r1 = DockerHubAuth.load_credentials("docker.io", config_dir=tmp_path)
        r2 = DockerHubAuth.load_credentials("ghcr.io", config_dir=tmp_path)
        assert r1 == ("user1", "token1")
        assert r2 == ("user2", "token2")
