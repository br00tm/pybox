"""OCI tar layer extraction with whiteout file handling.

OCI image layers are gzip-compressed tarballs. When extracting multiple
layers in order (base first, then diffs), whiteout files encode deletions:

    .wh.<filename>       → delete <filename> in the merged view
    .wh..wh..opq         → opaque whiteout — delete entire parent directory
                           (the directory is recreated with the layer's content)

This module extracts layers to a flat directory structure:
    /var/lib/pybox/images/layers/<digest>/

The storage/overlay module then mounts them via OverlayFS.

Reference: OCI Image Spec §5.4 Layer Changesets
           https://github.com/opencontainers/image-spec/blob/main/layer.md
"""

from __future__ import annotations

import gzip
import logging
import os
import tarfile
from pathlib import Path

from pybox.exceptions import StorageError

logger = logging.getLogger(__name__)

# OCI whiteout prefix — files starting with this encode deletions
WHITEOUT_PREFIX = ".wh."

# Opaque whiteout marker — signals that the entire directory is replaced
OPAQUE_WHITEOUT = ".wh..wh..opq"


def extract_layer(layer_path: Path, dest_dir: Path) -> None:
    """Extract a single OCI layer tarball to dest_dir.

    Handles:
    - gzip-compressed tarballs (.tar.gz / .tgz)
    - Plain tarballs (.tar)
    - Whiteout files (deletions in layer diffs)
    - Opaque whiteout (directory replacement)

    The destination directory is created if it doesn't exist. Existing files
    are overwritten — layers are applied in order (base first).

    Args:
        layer_path: Path to the downloaded layer file.
        dest_dir:   Directory where the layer contents will be extracted.

    Raises:
        StorageError: If the tarball cannot be opened or extraction fails.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)

    logger.debug("Extracting layer %s → %s", layer_path.name, dest_dir)

    try:
        # Detect compression: gzip if the file starts with the magic bytes
        mode = _detect_open_mode(layer_path)
        with tarfile.open(layer_path, mode) as tf:
            _extract_with_whiteouts(tf, dest_dir)
    except (tarfile.TarError, gzip.BadGzipFile, OSError) as exc:
        raise StorageError(
            f"Failed to extract layer {layer_path.name}",
            details=str(exc),
        ) from exc

    logger.debug("Layer extracted: %s", dest_dir)


def _detect_open_mode(path: Path) -> str:
    """Detect the tarball open mode based on file magic bytes.

    Args:
        path: Path to the tarball file.

    Returns:
        tarfile open mode string: "r:gz", "r:bz2", or "r:".
    """
    with open(path, "rb") as f:
        header = f.read(3)

    if header[:2] == b"\x1f\x8b":
        # Gzip magic bytes: 0x1f 0x8b
        return "r:gz"
    if header[:3] == b"BZh":
        # bzip2 magic bytes
        return "r:bz2"
    # Assume plain tar (also handles zstd via tarfile's auto-detect on 3.12+)
    return "r:"


def _extract_with_whiteouts(tf: tarfile.TarFile, dest_dir: Path) -> None:
    """Extract tar members, processing whiteout files as deletions.

    Iterates over all tar members in order. For each member:
    - If it's an opaque whiteout (.wh..wh..opq): clear the parent directory.
    - If it's a whiteout (.wh.<name>): delete the named file/directory.
    - Otherwise: extract normally.

    Args:
        tf:       Opened TarFile object.
        dest_dir: Destination directory for extraction.
    """
    for member in tf.getmembers():
        # Strip leading "/" to prevent absolute path traversal attacks
        member.name = member.name.lstrip("/")
        if not member.name:
            continue

        name = os.path.basename(member.name)
        parent = os.path.dirname(member.name)

        if name == OPAQUE_WHITEOUT:
            # Opaque whiteout: delete everything in the parent directory.
            # The layer will then repopulate it with fresh content.
            _apply_opaque_whiteout(dest_dir / parent)
        elif name.startswith(WHITEOUT_PREFIX):
            # Regular whiteout: delete the named file or directory tree.
            real_name = name[len(WHITEOUT_PREFIX):]
            _apply_whiteout(dest_dir / parent / real_name)
        else:
            # Normal file — extract to dest_dir
            _safe_extract_member(tf, member, dest_dir)


def _apply_opaque_whiteout(directory: Path) -> None:
    """Remove all contents of directory (opaque whiteout).

    The directory itself is kept — it will be repopulated by subsequent
    members in the same layer tarball.

    Args:
        directory: Directory whose contents should be deleted.
    """
    if not directory.exists():
        return

    import shutil

    for child in directory.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink(missing_ok=True)

    logger.debug("Opaque whiteout applied to: %s", directory)


def _apply_whiteout(target: Path) -> None:
    """Delete a file or directory tree (regular whiteout).

    Args:
        target: Path to the file or directory to remove.
    """
    if not target.exists() and not target.is_symlink():
        return

    import shutil

    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target)
    else:
        target.unlink(missing_ok=True)

    logger.debug("Whiteout applied: %s", target)


def _safe_extract_member(
    tf: tarfile.TarFile, member: tarfile.TarInfo, dest_dir: Path
) -> None:
    """Extract a single tar member safely, preventing path traversal.

    Validates that the resolved destination path remains inside dest_dir.

    Args:
        tf:       Opened TarFile.
        member:   TarInfo member to extract.
        dest_dir: Extraction root directory.
    """
    dest = (dest_dir / member.name).resolve()

    # Security check: ensure the extracted path stays within dest_dir
    try:
        dest.relative_to(dest_dir.resolve())
    except ValueError:
        logger.warning("Skipping path traversal attempt: %s", member.name)
        return

    try:
        tf.extract(member, path=dest_dir, set_attrs=True)
    except (tarfile.ExtractError, OSError) as exc:
        # Some layers contain device nodes or special files that require root.
        # Log and continue rather than failing the entire extraction.
        logger.debug("Skipping member %s: %s", member.name, exc)


def apply_layers(layer_paths: list[Path], base_dir: Path) -> list[Path]:
    """Extract a list of OCI layers in order into separate directories.

    Each layer gets its own directory under base_dir:
        base_dir/<layer_digest>/

    The directories are returned in order (base layer first) and passed
    to OverlayFS as lowerdir entries.

    Args:
        layer_paths: Ordered list of layer tarball paths (base → top).
        base_dir:    Parent directory for individual layer directories.

    Returns:
        List of extracted layer directories in the same order as layer_paths.
    """
    extracted: list[Path] = []

    for layer_path in layer_paths:
        # Use the filename (digest) as the subdirectory name
        digest_name = layer_path.stem.replace(".tar", "")
        layer_dir = base_dir / digest_name

        if not (layer_dir / ".extracted").exists():
            extract_layer(layer_path, layer_dir)
            # Touch a sentinel file to mark this layer as fully extracted
            (layer_dir / ".extracted").touch()
        else:
            logger.debug("Layer already extracted: %s", layer_dir)

        extracted.append(layer_dir)

    return extracted
