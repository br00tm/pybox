"""Unit tests for pybox.image.commit."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path



def _make_layer_tar_gz(path: Path, files: dict[str, bytes]) -> Path:
    """Create a gzip-compressed tar with the given files."""
    with tarfile.open(path, "w:gz") as tf:
        for name, content in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
    return path


def _make_build_spec(base: str = "ubuntu:24.04"):
    """Create a minimal BuildSpec for testing."""
    from pybox.image.build_spec import BuildSpec
    return BuildSpec.model_validate({
        "box": {"name": "testapp", "base": base},
        "run": {"cmd": ["python3", "app.py"], "expose": [8000], "env": {"FOO": "bar"}},
    })


class TestCommitImage:
    def test_creates_manifest_json(self, tmp_path: Path) -> None:
        from pybox.image.commit import commit_image

        layer = _make_layer_tar_gz(tmp_path / "layer0.tar.gz", {"app.py": b"print('hello')"})
        spec = _make_build_spec()

        digest = commit_image(
            layer_tars=[layer],
            base_config={},
            build_spec=spec,
            tag="testapp:v1",
            images_dir=tmp_path / "images",
        )

        images_dir = tmp_path / "images"
        config_hex = digest.split(":")[-1]
        manifest_path = images_dir / "sha256" / config_hex / "manifest.json"
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text())
        assert manifest["schemaVersion"] == 2
        assert "layers" in manifest
        assert len(manifest["layers"]) == 1

    def test_creates_config_json(self, tmp_path: Path) -> None:
        from pybox.image.commit import commit_image

        layer = _make_layer_tar_gz(tmp_path / "l.tar.gz", {"f": b"data"})
        spec = _make_build_spec()

        digest = commit_image(
            layer_tars=[layer],
            base_config={},
            build_spec=spec,
            tag="myapp:latest",
            images_dir=tmp_path / "images",
        )

        images_dir = tmp_path / "images"
        config_hex = digest.split(":")[-1]
        config_path = images_dir / "sha256" / config_hex / "config.json"
        assert config_path.exists()
        config = json.loads(config_path.read_text())
        assert config["architecture"] == "amd64"
        assert config["os"] == "linux"
        assert config["config"]["Cmd"] == ["python3", "app.py"]
        assert "8000/tcp" in config["config"]["ExposedPorts"]
        assert "FOO=bar" in config["config"]["Env"]

    def test_layer_count_matches(self, tmp_path: Path) -> None:
        from pybox.image.commit import commit_image

        layers = [
            _make_layer_tar_gz(tmp_path / f"layer{i}.tar.gz", {f"file{i}": b"x"})
            for i in range(3)
        ]
        spec = _make_build_spec()

        digest = commit_image(
            layer_tars=layers,
            base_config={},
            build_spec=spec,
            tag="myapp:v3",
            images_dir=tmp_path / "images",
        )

        images_dir = tmp_path / "images"
        config_hex = digest.split(":")[-1]
        manifest = json.loads(
            (images_dir / "sha256" / config_hex / "manifest.json").read_text()
        )
        assert len(manifest["layers"]) == 3

    def test_diff_ids_length_matches_layers(self, tmp_path: Path) -> None:
        from pybox.image.commit import commit_image

        layers = [
            _make_layer_tar_gz(tmp_path / f"l{i}.tar.gz", {f"f{i}": b"y"})
            for i in range(2)
        ]
        spec = _make_build_spec()

        digest = commit_image(
            layer_tars=layers,
            base_config={},
            build_spec=spec,
            tag="x:1",
            images_dir=tmp_path / "images",
        )

        images_dir = tmp_path / "images"
        config_hex = digest.split(":")[-1]
        config = json.loads(
            (images_dir / "sha256" / config_hex / "config.json").read_text()
        )
        assert len(config["rootfs"]["diff_ids"]) == 2

    def test_updates_tags_json(self, tmp_path: Path) -> None:
        from pybox.image.commit import commit_image

        layer = _make_layer_tar_gz(tmp_path / "l.tar.gz", {"a": b"b"})
        spec = _make_build_spec()
        images_dir = tmp_path / "images"

        digest = commit_image(
            layer_tars=[layer],
            base_config={},
            build_spec=spec,
            tag="myapp:tagged",
            images_dir=images_dir,
        )

        tags_file = images_dir / "tags.json"
        assert tags_file.exists()
        tags = json.loads(tags_file.read_text())
        assert "myapp:tagged" in tags
        assert tags["myapp:tagged"] == digest

    def test_merges_base_env(self, tmp_path: Path) -> None:
        from pybox.image.commit import commit_image

        layer = _make_layer_tar_gz(tmp_path / "l.tar.gz", {"f": b""})
        spec = _make_build_spec()

        base_config = {
            "config": {
                "Env": ["BASE_VAR=base_value"],
                "Cmd": ["/bin/sh"],
            }
        }
        digest = commit_image(
            layer_tars=[layer],
            base_config=base_config,
            build_spec=spec,
            tag="app:merged",
            images_dir=tmp_path / "images",
        )

        images_dir = tmp_path / "images"
        config_hex = digest.split(":")[-1]
        config = json.loads(
            (images_dir / "sha256" / config_hex / "config.json").read_text()
        )
        env = config["config"]["Env"]
        assert any("BASE_VAR" in e for e in env)
        assert any("FOO" in e for e in env)
