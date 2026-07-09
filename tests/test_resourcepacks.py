from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
import stat
import warnings
import zipfile

import pytest

import mcdata.resourcepacks as resourcepacks_module
from mcdata.resourcepacks import (
    NORMALIZER_VERSION,
    RESOLUTION_SIDECAR,
    ResourcePackError,
    ResourcePackFormat,
    ResourcePackSource,
    discover_target_resource_format,
    materialize_resourcepacks,
    resourcepack_source_dir,
)


TARGET = (88, 0)


@pytest.mark.parametrize(
    ("pack_metadata", "case"),
    [
        (
            {"description": "Faithful", "pack_format": 88},
            "current pack_format without mandatory bounds",
        ),
        (
            {
                "description": "Visual Enchantments",
                "pack_format": 84,
                "min_format": 84,
                "max_format": 84,
            },
            "old scalar bounds",
        ),
        (
            {
                "description": "Simplista",
                "pack_format": 69,
                "min_format": [15, 0],
                "max_format": [84, 0],
                "supported_formats": [15, 84],
            },
            "old tuple bounds",
        ),
        (
            {
                "description": "New Glowing Ores",
                "pack_format": 34,
                "supported_formats": {"min_inclusive": 8, "max_inclusive": 69},
            },
            "legacy supported_formats object",
        ),
        (
            {
                "description": "Midnighttiggers FCT",
                "pack_format": 14,
                "supported_formats": [14, 100],
            },
            "legacy supported_formats list",
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_known_obsolete_metadata_is_normalized(
    tmp_path: Path,
    pack_metadata: dict,
    case: str,
) -> None:
    del case
    document = {"pack": pack_metadata, "overlay": {"kept": True}}
    source = _write_source_pack(
        tmp_path,
        document,
        members=[("z-last.txt", b"last"), ("a-first.txt", b"first")],
    )
    original_sha = _sha256(source.path)

    names, resolution = materialize_resourcepacks(
        tmp_path,
        sources=[source],
        target=_target(tmp_path),
        game_version="26.2",
    )

    assert names == [source.filename]
    effective = tmp_path / "resourcepacks" / source.filename
    with zipfile.ZipFile(effective) as archive:
        after = json.loads(archive.read("pack.mcmeta"))
        assert archive.namelist() == ["a-first.txt", "pack.mcmeta", "z-last.txt"]
        for info in archive.infolist():
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
            assert stat.S_IMODE(info.external_attr >> 16) == 0o644
            assert info.compress_type == zipfile.ZIP_DEFLATED
        assert archive.read("pack.mcmeta") == (
            json.dumps(after, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")

    assert after["pack"]["pack_format"] == 88
    assert after["pack"]["min_format"] == [88, 0]
    assert after["pack"]["max_format"] == [88, 0]
    assert "supported_formats" not in after["pack"]
    assert after["pack"]["description"] == pack_metadata["description"]
    assert after["overlay"] == {"kept": True}
    assert _sha256(source.path) == original_sha

    record = resolution["packs"][0]
    assert record["normalized"] is True
    assert record["before"] == document
    assert record["after"] == after
    assert record["source_sha256"] == original_sha
    assert record["effective_sha256"] == _sha256(effective)
    assert resolution["normalizer_version"] == NORMALIZER_VERSION


def test_compatible_pack_is_a_byte_for_byte_no_op_and_stale_packs_are_removed(
    tmp_path: Path,
) -> None:
    document = {
        "pack": {
            "description": "already compatible",
            "pack_format": 88,
            "min_format": [84, 7],
            "max_format": [999, 0],
            "supported_formats": [84, 999],
        }
    }
    source = _write_source_pack(
        tmp_path,
        document,
        members=[("custom.bin", b"\x00\x01")],
        timestamp=(2024, 5, 6, 7, 8, 10),
    )
    source_bytes = source.path.read_bytes()
    effective_dir = tmp_path / "resourcepacks"
    effective_dir.mkdir()
    (effective_dir / "stale.zip").write_bytes(b"stale")
    (effective_dir / "also-stale.txt").write_text("stale", encoding="utf-8")

    names, resolution = materialize_resourcepacks(
        tmp_path,
        sources=[source],
        target=_target(tmp_path),
        game_version="26.2",
    )

    effective = effective_dir / source.filename
    assert names == [source.filename]
    assert [path.name for path in effective_dir.iterdir()] == [source.filename]
    assert effective.read_bytes() == source_bytes
    assert resolution["packs"][0]["normalized"] is False
    assert resolution["packs"][0]["before"] == document
    assert resolution["packs"][0]["after"] == document
    assert resolution["packs"][0]["source_sha256"] == resolution["packs"][0]["effective_sha256"]


def test_normalized_zip_and_sidecar_are_identical_on_repeat(tmp_path: Path) -> None:
    source = _write_source_pack(
        tmp_path,
        {"pack": {"pack_format": 88, "description": "needs bounds"}},
        members=[("textures/example.txt", b"same bytes every time")],
    )
    target = _target(tmp_path)
    source_before = source.path.read_bytes()

    materialize_resourcepacks(
        tmp_path,
        sources=[source],
        target=target,
        game_version="26.2",
    )
    effective_path = tmp_path / "resourcepacks" / source.filename
    first_effective = effective_path.read_bytes()
    first_sidecar = (tmp_path / RESOLUTION_SIDECAR).read_bytes()

    materialize_resourcepacks(
        tmp_path,
        sources=[source],
        target=target,
        game_version="26.2",
    )

    assert effective_path.read_bytes() == first_effective
    assert (tmp_path / RESOLUTION_SIDECAR).read_bytes() == first_sidecar
    assert source.path.read_bytes() == source_before


def test_sidecar_write_failure_rolls_back_effective_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _write_source_pack(
        tmp_path,
        {"pack": {"pack_format": 88, "description": "needs bounds"}},
    )
    effective_dir = tmp_path / "resourcepacks"
    effective_dir.mkdir()
    (effective_dir / "old.zip").write_bytes(b"previous effective pack")
    sidecar = tmp_path / RESOLUTION_SIDECAR
    sidecar.parent.mkdir(exist_ok=True)
    sidecar.write_bytes(b'{"previous":true}\n')

    def fail_write(_path: Path, _value: dict) -> None:
        raise OSError("injected sidecar failure")

    monkeypatch.setattr(resourcepacks_module, "_atomic_write_json", fail_write)

    with pytest.raises(OSError, match="injected sidecar failure"):
        _materialize_one(tmp_path, source)

    assert [path.name for path in effective_dir.iterdir()] == ["old.zip"]
    assert (effective_dir / "old.zip").read_bytes() == b"previous effective pack"
    assert sidecar.read_bytes() == b'{"previous":true}\n'


@pytest.mark.parametrize(
    "members",
    [
        [("assets/example.txt", b"data")],
        [("nested/pack.mcmeta", b'{"pack":{"min_format":88,"max_format":88}}')],
    ],
    ids=["missing", "nested-only"],
)
def test_exact_root_pack_metadata_is_required(
    tmp_path: Path,
    members: list[tuple[str, bytes]],
) -> None:
    source = _write_raw_source_pack(tmp_path, members=members)

    with pytest.raises(ResourcePackError, match="exactly one root pack.mcmeta"):
        _materialize_one(tmp_path, source)


def test_duplicate_pack_metadata_member_fails_closed(tmp_path: Path) -> None:
    source_dir = resourcepack_source_dir(tmp_path)
    source_dir.mkdir(parents=True)
    path = source_dir / "example.zip"
    metadata = b'{"pack":{"min_format":88,"max_format":88}}'
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("pack.mcmeta", metadata)
            archive.writestr("pack.mcmeta", metadata)
    source = _source(path)

    with pytest.raises(ResourcePackError, match="duplicate member"):
        _materialize_one(tmp_path, source)


def test_oversized_pack_metadata_fails_closed(tmp_path: Path) -> None:
    source = _write_raw_source_pack(
        tmp_path,
        members=[("pack.mcmeta", b"x" * (1024 * 1024 + 1))],
    )

    with pytest.raises(ResourcePackError, match="pack.mcmeta exceeds size limit"):
        _materialize_one(tmp_path, source)


def test_archive_member_count_limit_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _write_source_pack(
        tmp_path,
        {"pack": {"pack_format": 88, "min_format": 88, "max_format": 88}},
        members=[("asset.txt", b"data")],
    )
    monkeypatch.setattr(resourcepacks_module, "_MAX_ARCHIVE_MEMBERS", 1)

    with pytest.raises(ResourcePackError, match="too many members"):
        _materialize_one(tmp_path, source)


@pytest.mark.parametrize("unsafe_name", ["../outside.txt", "/absolute.txt", "dir\\file.txt"])
def test_unsafe_member_path_fails_closed(tmp_path: Path, unsafe_name: str) -> None:
    source = _write_source_pack(
        tmp_path,
        {"pack": {"min_format": 88, "max_format": 88}},
        members=[(unsafe_name, b"unsafe")],
    )

    with pytest.raises(ResourcePackError, match="Unsafe resource-pack ZIP member path"):
        _materialize_one(tmp_path, source)


def test_source_game_version_must_match_target_game(tmp_path: Path) -> None:
    source = _write_source_pack(
        tmp_path,
        {"pack": {"min_format": 88, "max_format": 88}},
        game_versions=("1.21.11",),
    )

    with pytest.raises(ResourcePackError, match="does not declare game version"):
        _materialize_one(tmp_path, source)


def test_source_sha512_must_match_upstream_receipt(tmp_path: Path) -> None:
    source = _write_source_pack(
        tmp_path,
        {"pack": {"pack_format": 88, "min_format": 88, "max_format": 88}},
    )
    source = replace(source, expected_sha512="0" * 128)

    with pytest.raises(ResourcePackError, match="does not match upstream receipt"):
        _materialize_one(tmp_path, source)


def test_source_must_live_in_instance_source_directory(tmp_path: Path) -> None:
    outside = tmp_path / "outside.zip"
    with zipfile.ZipFile(outside, "w") as archive:
        archive.writestr("pack.mcmeta", '{"pack":{"min_format":88,"max_format":88}}')
    source = _source(outside)

    with pytest.raises(ResourcePackError, match="must be"):
        _materialize_one(tmp_path, source)


def test_ambiguous_comma_in_source_filename_fails_closed(tmp_path: Path) -> None:
    source = _write_source_pack(
        tmp_path,
        {"pack": {"pack_format": 88, "min_format": 88, "max_format": 88}},
        filename="ambiguous,name.zip",
    )

    with pytest.raises(ResourcePackError, match="Unsafe resource-pack filename"):
        _materialize_one(tmp_path, source)


def test_empty_selection_leaves_only_an_empty_effective_directory(tmp_path: Path) -> None:
    effective_dir = tmp_path / "resourcepacks"
    effective_dir.mkdir()
    (effective_dir / "stale.zip").write_bytes(b"stale")

    names, resolution = materialize_resourcepacks(
        tmp_path,
        sources=[],
        target=_target(tmp_path),
        game_version="26.2",
    )

    assert names == []
    assert list(effective_dir.iterdir()) == []
    assert resolution["packs"] == []


def test_discovers_resource_format_from_fabric_client_jar(tmp_path: Path) -> None:
    main_dir = tmp_path / "launcher"
    fabric_jar = main_dir / "versions/fabric-26.2-0.19.3/fabric-26.2-0.19.3.jar"
    _write_version_jar(fabric_jar, game_version="26.2", major=88, minor=0)
    _write_version_jar(
        main_dir / "versions/fabric-1.21.11-0.18.4/fabric-1.21.11-0.18.4.jar",
        game_version="1.21.11",
        major=75,
        minor=0,
    )

    target = discover_target_resource_format(main_dir, "26.2")

    assert target.version == TARGET
    assert target.source_jar == fabric_jar.resolve()


def test_prefers_official_client_jar_when_client_and_fabric_are_present(tmp_path: Path) -> None:
    main_dir = tmp_path / "launcher"
    official = main_dir / "versions/26.2/26.2.jar"
    fabric = main_dir / "versions/fabric-26.2-0.19.3/fabric-26.2-0.19.3.jar"
    _write_version_jar(fabric, game_version="26.2", major=88, minor=0)
    _write_version_jar(official, game_version="26.2", major=88, minor=0)

    target = discover_target_resource_format(main_dir, "26.2")

    assert target.source_jar == official.resolve()


def test_discovery_fails_if_matching_launcher_jars_disagree(tmp_path: Path) -> None:
    main_dir = tmp_path / "launcher"
    _write_version_jar(
        main_dir / "versions/26.2/26.2.jar",
        game_version="26.2",
        major=88,
        minor=0,
    )
    _write_version_jar(
        main_dir / "versions/fabric-26.2-0.19.3/fabric-26.2-0.19.3.jar",
        game_version="26.2",
        major=89,
        minor=0,
    )

    with pytest.raises(ResourcePackError, match="disagree"):
        discover_target_resource_format(main_dir, "26.2")


def test_sidecar_records_source_effective_and_target_provenance(tmp_path: Path) -> None:
    source = _write_source_pack(
        tmp_path,
        {"pack": {"description": "old", "min_format": 84, "max_format": 84}},
    )
    source = replace(
        source,
        expected_sha512=_sha512(source.path),
        download_url="https://cdn.modrinth.com/example.zip",
        expected_size=source.path.stat().st_size,
    )
    target = _target(tmp_path)

    names, resolution = _materialize_one(tmp_path, source, target=target)
    saved = json.loads((tmp_path / RESOLUTION_SIDECAR).read_text(encoding="utf-8"))

    assert names == ["example.zip"]
    assert saved == resolution
    assert saved["schema_version"] == 1
    assert saved["game_version"] == "26.2"
    assert saved["target"] == {
        "resource_major": 88,
        "resource_minor": 0,
        "source_jar": str(target.source_jar.resolve()),
        "source_jar_sha256": _sha256(target.source_jar),
    }
    record = saved["packs"][0]
    assert record["project"] == "example-project"
    assert record["version"] == "v1"
    assert record["game_versions"] == ["26.2"]
    assert Path(record["source_path"]) == source.path.resolve()
    assert Path(record["effective_path"]) == (tmp_path / "resourcepacks/example.zip").resolve()
    assert record["source_sha512"] == _sha512(source.path)
    assert record["upstream_sha512"] == _sha512(source.path)
    assert record["download_url"] == "https://cdn.modrinth.com/example.zip"
    assert record["expected_size"] == source.path.stat().st_size


def _write_source_pack(
    work_dir: Path,
    document: dict,
    *,
    filename: str = "example.zip",
    members: list[tuple[str, bytes]] | None = None,
    game_versions: tuple[str, ...] = ("26.2",),
    timestamp: tuple[int, int, int, int, int, int] = (2023, 2, 4, 6, 8, 10),
) -> ResourcePackSource:
    metadata = json.dumps(document, ensure_ascii=False).encode("utf-8")
    return _write_raw_source_pack(
        work_dir,
        filename=filename,
        members=[("pack.mcmeta", metadata), *(members or [])],
        game_versions=game_versions,
        timestamp=timestamp,
    )


def _write_raw_source_pack(
    work_dir: Path,
    *,
    filename: str = "example.zip",
    members: list[tuple[str, bytes]],
    game_versions: tuple[str, ...] = ("26.2",),
    timestamp: tuple[int, int, int, int, int, int] = (2023, 2, 4, 6, 8, 10),
) -> ResourcePackSource:
    source_dir = resourcepack_source_dir(work_dir)
    source_dir.mkdir(parents=True, exist_ok=True)
    path = source_dir / filename
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for name, data in members:
            info = zipfile.ZipInfo(name, date_time=timestamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            archive.writestr(info, data)
    return _source(path, game_versions=game_versions)


def _source(
    path: Path,
    *,
    game_versions: tuple[str, ...] = ("26.2",),
) -> ResourcePackSource:
    return ResourcePackSource(
        filename=path.name,
        path=path,
        project="example-project",
        version="v1",
        game_versions=game_versions,
    )


def _target(tmp_path: Path) -> ResourcePackFormat:
    jar = tmp_path / "client.jar"
    if not jar.exists():
        jar.write_bytes(b"official client jar fixture")
    return ResourcePackFormat(88, 0, jar)


def _materialize_one(
    work_dir: Path,
    source: ResourcePackSource,
    *,
    target: ResourcePackFormat | None = None,
) -> tuple[list[str], dict]:
    return materialize_resourcepacks(
        work_dir,
        sources=[source],
        target=target or _target(work_dir),
        game_version="26.2",
    )


def _write_version_jar(
    path: Path,
    *,
    game_version: str,
    major: int,
    minor: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "id": game_version,
        "pack_version": {
            "resource_major": major,
            "resource_minor": minor,
            "data_major": 107,
            "data_minor": 1,
        },
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("version.json", json.dumps(document))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha512(path: Path) -> str:
    return hashlib.sha512(path.read_bytes()).hexdigest()
