"""Exception hierarchy for PyBox container runtime."""

from __future__ import annotations


class PyBoxError(Exception):
    """Base exception for all PyBox errors."""

    def __init__(self, message: str, *, details: str | None = None) -> None:
        super().__init__(message)
        self.details = details

    def __str__(self) -> str:
        base = super().__str__()
        if self.details:
            return f"{base}: {self.details}"
        return base


class ImageNotFoundError(PyBoxError):
    """Raised when a requested image is not found locally or in the registry."""

    def __init__(self, image: str) -> None:
        super().__init__(f"Image not found: {image}")
        self.image = image


class ImagePullError(PyBoxError):
    """Raised when an image pull from the registry fails."""


class ContainerError(PyBoxError):
    """Raised when a container operation fails."""


class ContainerNotFoundError(PyBoxError):
    """Raised when a container ID does not exist."""

    def __init__(self, container_id: str) -> None:
        super().__init__(f"Container not found: {container_id}")
        self.container_id = container_id


class NamespaceError(PyBoxError):
    """Raised when a Linux namespace operation fails."""


class CgroupError(PyBoxError):
    """Raised when a cgroup operation fails."""


class StorageError(PyBoxError):
    """Raised when a storage (OverlayFS/layer) operation fails."""


class RegistryError(PyBoxError):
    """Raised when an OCI registry operation fails."""

    def __init__(self, message: str, *, status_code: int | None = None, details: str | None = None) -> None:
        super().__init__(message, details=details)
        self.status_code = status_code


class NetworkError(PyBoxError):
    """Raised when a network operation fails."""
