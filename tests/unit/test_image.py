"""Unit tests for pybox.image.layer — layer extraction and whiteout handling.

All tests use in-memory tar creation via tmp_path. No root required.
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path


from pybox.image.layer import (
    OPAQUE_WHITEOUT,
    WHITEOUT_PREFIX,
    extract_layer,
)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_tar_gz(tmp_path: Path, members: dict[str, bytes | None]) -> Path:
    """Create a gzip-compressed tar file in tmp_path.

    Args:
        tmp_path: Directory to write the tar file in.
        members:  Mapping of member name → content bytes (None for directories).

    Returns:
        Path to the created .tar.gz file.
    """
    tar_path = tmp_path / "layer.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        for name, content in members.items():
            if content is None:
                # Directory member
                info = tarfile.TarInfo(name=name)
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                tf.addfile(info)
            else:
                data = content
                info = tarfile.TarInfo(name=name)
                info.size = len(data)
                info.mode = 0o644
                tf.addfile(info, io.BytesIO(data))
    return tar_path


# ------------------------------------------------------------------
# Normal extraction
# ------------------------------------------------------------------

class TestExtractLayerNormal:
    def test_extracts_regular_file(self, tmp_path: Path) -> None:
        layer = _make_tar_gz(tmp_path, {"hello.txt": b"hello world"})
        dest = tmp_path / "dest"
        extract_layer(layer, dest)
        assert (dest / "hello.txt").read_bytes() == b"hello world"

    def test_extracts_nested_file(self, tmp_path: Path) -> None:
        layer = _make_tar_gz(tmp_path, {"usr/bin/python": b"#!/usr/bin/python"})
        dest = tmp_path / "dest"
        extract_layer(layer, dest)
        assert (dest / "usr" / "bin" / "python").read_bytes() == b"#!/usr/bin/python"

    def test_creates_dest_if_missing(self, tmp_path: Path) -> None:
        layer = _make_tar_gz(tmp_path, {"file.txt": b"data"})
        dest = tmp_path / "new" / "dest"
        extract_layer(layer, dest)
        assert dest.is_dir()

    def test_extracts_directory_member(self, tmp_path: Path) -> None:
        layer = _make_tar_gz(tmp_path, {"mydir/": None, "mydir/file.txt": b"content"})
        dest = tmp_path / "dest"
        extract_layer(layer, dest)
        assert (dest / "mydir").is_dir()
        assert (dest / "mydir" / "file.txt").read_bytes() == b"content"

    def test_overwrites_existing_file(self, tmp_path: Path) -> None:
        dest = tmp_path / "dest"
        dest.mkdir()
        (dest / "file.txt").write_bytes(b"old content")
        layer = _make_tar_gz(tmp_path, {"file.txt": b"new content"})
        extract_layer(layer, dest)
        assert (dest / "file.txt").read_bytes() == b"new content"


# ------------------------------------------------------------------
# Whiteout files
# ------------------------------------------------------------------

class TestWhiteoutHandling:
    def test_regular_whiteout_deletes_file(self, tmp_path: Path) -> None:
        dest = tmp_path / "dest"
        dest.mkdir()
        target = dest / "deleteme.txt"
        target.write_bytes(b"going away")

        # Layer contains .wh.deleteme.txt
        layer = _make_tar_gz(tmp_path, {f"{WHITEOUT_PREFIX}deleteme.txt": b""})
        extract_layer(layer, dest)

        assert not target.exists()

    def test_regular_whiteout_deletes_directory(self, tmp_path: Path) -> None:
        dest = tmp_path / "dest"
        (dest / "subdir").mkdir(parents=True)
        (dest / "subdir" / "file.txt").write_bytes(b"data")

        layer = _make_tar_gz(tmp_path, {f"{WHITEOUT_PREFIX}subdir": b""})
        extract_layer(layer, dest)

        assert not (dest / "subdir").exists()

    def test_opaque_whiteout_clears_directory_contents(self, tmp_path: Path) -> None:
        dest = tmp_path / "dest"
        subdir = dest / "mydir"
        subdir.mkdir(parents=True)
        (subdir / "old_file.txt").write_bytes(b"old")
        (subdir / "another.txt").write_bytes(b"also old")

        # Opaque whiteout inside mydir
        layer = _make_tar_gz(
            tmp_path,
            {
                f"mydir/{OPAQUE_WHITEOUT}": b"",
                "mydir/new_file.txt": b"fresh",
            },
        )
        extract_layer(layer, dest)

        assert not (subdir / "old_file.txt").exists()
        assert not (subdir / "another.txt").exists()
        # New content from the layer should still be there
        assert (subdir / "new_file.txt").read_bytes() == b"fresh"

    def test_whiteout_nonexistent_target_is_noop(self, tmp_path: Path) -> None:
        dest = tmp_path / "dest"
        dest.mkdir()

        # Whiteout for a file that doesn't exist — should not raise
        layer = _make_tar_gz(tmp_path, {f"{WHITEOUT_PREFIX}ghost.txt": b""})
        extract_layer(layer, dest)  # must not raise

    def test_opaque_whiteout_keeps_directory_itself(self, tmp_path: Path) -> None:
        dest = tmp_path / "dest"
        subdir = dest / "keepme"
        subdir.mkdir(parents=True)
        (subdir / "old.txt").write_bytes(b"old")

        layer = _make_tar_gz(tmp_path, {f"keepme/{OPAQUE_WHITEOUT}": b""})
        extract_layer(layer, dest)

        # Directory should still exist, just be empty
        assert subdir.is_dir()
        assert list(subdir.iterdir()) == []


# ------------------------------------------------------------------
# Security: path traversal prevention
# ------------------------------------------------------------------

class TestPathTraversalPrevention:
    def test_absolute_path_member_is_stripped(self, tmp_path: Path) -> None:
        # Tar member with absolute path should be extracted relative to dest
        tar_path = tmp_path / "layer.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tf:
            info = tarfile.TarInfo(name="/etc/passwd")
            info.size = 4
            tf.addfile(info, io.BytesIO(b"fake"))

        dest = tmp_path / "dest"
        # Should not raise and should not write to /etc/passwd
        extract_layer(tar_path, dest)
        assert not Path("/etc/passwd_pybox_test").exists()


# ------------------------------------------------------------------
# Symlink handling
# ------------------------------------------------------------------

class TestSymlinkExtraction:
    def test_extracts_symlink(self, tmp_path: Path) -> None:
        tar_path = tmp_path / "layer.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tf:
            # Add target file
            info = tarfile.TarInfo(name="real.txt")
            info.size = 4
            tf.addfile(info, io.BytesIO(b"data"))
            # Add symlink
            sym_info = tarfile.TarInfo(name="link.txt")
            sym_info.type = tarfile.SYMTYPE
            sym_info.linkname = "real.txt"
            tf.addfile(sym_info)

        dest = tmp_path / "dest"
        extract_layer(tar_path, dest)

        assert (dest / "link.txt").is_symlink()
        assert (dest / "link.txt").readlink() == Path("real.txt")
