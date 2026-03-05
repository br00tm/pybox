"""Pydantic models for OCI image manifests and configs.

Covers both OCI Image Spec and Docker Distribution Spec v2 schemas:
    - OciManifest       — the image manifest (list of layers + config ref)
    - OciConfig         — image config (env, cmd, entrypoint, rootfs diff IDs)
    - OciLayer          — a single layer descriptor
    - OciImageIndex     — multi-arch manifest list

Reference:
    https://github.com/opencontainers/image-spec/blob/main/manifest.md
    https://github.com/opencontainers/image-spec/blob/main/config.md
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# OCI / Docker media type constants
MEDIA_TYPE_MANIFEST_V2 = "application/vnd.docker.distribution.manifest.v2+json"
MEDIA_TYPE_MANIFEST_OCI = "application/vnd.oci.image.manifest.v1+json"
MEDIA_TYPE_INDEX = "application/vnd.docker.distribution.manifest.list.v2+json"
MEDIA_TYPE_INDEX_OCI = "application/vnd.oci.image.index.v1+json"
MEDIA_TYPE_LAYER = "application/vnd.docker.image.rootfs.diff.tar.gzip"
MEDIA_TYPE_LAYER_OCI = "application/vnd.oci.image.layer.v1.tar+gzip"
MEDIA_TYPE_CONFIG = "application/vnd.docker.container.image.v1+json"
MEDIA_TYPE_CONFIG_OCI = "application/vnd.oci.image.config.v1+json"


class OciDescriptor(BaseModel):
    """Generic OCI content descriptor (manifest, config, or layer reference)."""

    media_type: str = Field(alias="mediaType", default="")
    digest: str
    size: int
    urls: list[str] | None = Field(default=None)

    model_config = {"populate_by_name": True}


class OciLayer(OciDescriptor):
    """A single image layer descriptor in the manifest."""

    @property
    def digest_hex(self) -> str:
        """Return the hex portion of the digest (after 'sha256:')."""
        return self.digest.split(":")[-1]

    @property
    def is_gzip(self) -> bool:
        """Return True if the layer is a gzip-compressed tar."""
        return self.media_type in (MEDIA_TYPE_LAYER, MEDIA_TYPE_LAYER_OCI)


class OciManifest(BaseModel):
    """OCI/Docker image manifest (schema version 2).

    Contains a reference to the image config and an ordered list of layer
    descriptors. Layers are listed base-first (index 0 = bottom layer).
    """

    schema_version: int = Field(alias="schemaVersion", default=2)
    media_type: str = Field(alias="mediaType", default=MEDIA_TYPE_MANIFEST_V2)
    config: OciDescriptor
    layers: list[OciLayer] = Field(default_factory=list)

    model_config = {"populate_by_name": True}

    def layer_digests(self) -> list[str]:
        """Return digests for all layers, base layer first."""
        return [layer.digest for layer in self.layers]


class OciRootfs(BaseModel):
    """Rootfs section of OCI image config (diff IDs for layers)."""

    type: str = "layers"
    diff_ids: list[str] = Field(alias="diff_ids", default_factory=list)

    model_config = {"populate_by_name": True}


class OciConfigSection(BaseModel):
    """Runtime configuration embedded in an OCI image config."""

    cmd: list[str] | None = Field(alias="Cmd", default=None)
    entrypoint: list[str] | None = Field(alias="Entrypoint", default=None)
    env: list[str] | None = Field(alias="Env", default=None)
    working_dir: str = Field(alias="WorkingDir", default="")
    user: str = Field(alias="User", default="")
    exposed_ports: dict[str, Any] = Field(alias="ExposedPorts", default_factory=dict)
    labels: dict[str, str] = Field(alias="Labels", default_factory=dict)

    model_config = {"populate_by_name": True}


class OciConfig(BaseModel):
    """Full OCI image configuration JSON (the blob referenced by manifest.config).

    Contains runtime defaults (Cmd, Env, etc.) and the rootfs diff ID list
    which maps layer order to their uncompressed sha256 hashes.
    """

    architecture: str = "amd64"
    os: str = "linux"
    config: OciConfigSection = Field(default_factory=OciConfigSection)
    rootfs: OciRootfs = Field(default_factory=OciRootfs)
    history: list[dict[str, Any]] = Field(default_factory=list)

    model_config = {"populate_by_name": True}

    def diff_ids(self) -> list[str]:
        """Return the ordered list of uncompressed layer diff IDs."""
        return self.rootfs.diff_ids

    def env_dict(self) -> dict[str, str]:
        """Parse the Env list ('KEY=VAL') into a dict."""
        result: dict[str, str] = {}
        for entry in self.config.env or []:
            if "=" in entry:
                k, _, v = entry.partition("=")
                result[k] = v
        return result


class OciIndexEntry(BaseModel):
    """A single entry in a multi-arch manifest index."""

    media_type: str = Field(alias="mediaType")
    digest: str
    size: int
    platform: dict[str, str] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class OciImageIndex(BaseModel):
    """Multi-architecture manifest list (manifest index).

    Used to provide platform-specific manifests under a single tag.
    """

    schema_version: int = Field(alias="schemaVersion", default=2)
    media_type: str = Field(alias="mediaType", default=MEDIA_TYPE_INDEX)
    manifests: list[OciIndexEntry] = Field(default_factory=list)

    model_config = {"populate_by_name": True}

    def find_platform(self, os: str = "linux", arch: str = "amd64") -> OciIndexEntry | None:
        """Find the manifest entry for a given platform.

        Args:
            os:   Target OS string, e.g. "linux".
            arch: Target architecture string, e.g. "amd64".

        Returns:
            Matching OciIndexEntry or None if not found.
        """
        for entry in self.manifests:
            if entry.platform.get("os") == os and entry.platform.get("architecture") == arch:
                return entry
        return None


def parse_manifest(data: dict[str, Any]) -> OciManifest:
    """Parse a raw manifest dict into an OciManifest model.

    Args:
        data: Parsed JSON dict from the registry manifest endpoint.

    Returns:
        Validated OciManifest instance.

    Raises:
        ValidationError: If the manifest structure is invalid.
    """
    return OciManifest.model_validate(data)
