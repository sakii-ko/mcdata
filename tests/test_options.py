from pathlib import Path

from mcdata.render.options import QUIET_CAPTURE_OPTIONS, write_iris_config, write_options


def _read_options(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, value = line.split(":", 1)
        values[key] = value
    return values


def test_write_options_includes_quiet_capture_keys(tmp_path: Path) -> None:
    write_options(tmp_path, quality="low", resourcepacks=[])
    options = _read_options(tmp_path / "options.txt")

    for key, value in QUIET_CAPTURE_OPTIONS.items():
        assert options[key] == value
    assert options["rawMouseInput"] == "true"
    assert options["mouseSensitivity"] == "0.5"


def test_write_options_resourcepack_format(tmp_path: Path) -> None:
    write_options(tmp_path, quality="low", resourcepacks=["a.zip", "b.zip"])
    options = _read_options(tmp_path / "options.txt")

    assert options["resourcePacks"] == '["file/a.zip","file/b.zip"]'
    assert options["incompatibleResourcePacks"] == "[]"


def test_write_iris_config_with_shaderpack(tmp_path: Path) -> None:
    write_iris_config(tmp_path, shaderpack="example.zip", enabled=True)

    text = (tmp_path / "config" / "iris.properties").read_text(encoding="utf-8")
    assert "enableShaders=true" in text
    assert "shaderPack=example.zip" in text


def test_write_iris_config_without_shaderpack(tmp_path: Path) -> None:
    write_iris_config(tmp_path, shaderpack=None, enabled=True)

    text = (tmp_path / "config" / "iris.properties").read_text(encoding="utf-8")
    assert "enableShaders=false" in text
    assert "shaderPack=" in text
