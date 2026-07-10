import hashlib
import json
from pathlib import Path

import pytest

from mcdata import packs
from mcdata.modrinth import ProjectVersion, VersionFile


def _version(
    *,
    game_versions: list[str],
    payload: bytes = b"upstream bytes",
    filename: str = "example.zip",
) -> ProjectVersion:
    return ProjectVersion(
        project="example-pack",
        version_number="1.2.3",
        version_type="release",
        game_versions=game_versions,
        loaders=[],
        files=[
            VersionFile(
                filename=filename,
                url="https://example.invalid/example.zip",
                primary=True,
                sha512=hashlib.sha512(payload).hexdigest(),
                size=len(payload),
            )
        ],
    )


def _multi_file_version() -> tuple[ProjectVersion, dict[str, bytes]]:
    payloads = {
        "Patrix_26.2_32x_basic.zip": b"basic bytes",
        "Patrix_26.2_32x_addon.zip": b"addon bytes",
        "Patrix_26.2_32x_bonus.zip": b"bonus bytes",
        "Patrix_26.2_models.zip": b"models bytes",
    }
    files = [
        VersionFile(
            filename=filename,
            url=f"https://example.invalid/{filename}",
            primary=filename.endswith("_basic.zip"),
            sha512=hashlib.sha512(payload).hexdigest(),
            size=len(payload),
        )
        for filename, payload in payloads.items()
    ]
    return (
        ProjectVersion(
            project="patrix-32x",
            version_number="89",
            version_type="release",
            game_versions=["26.2"],
            loaders=[],
            files=files,
        ),
        payloads,
    )


def _asset_config() -> dict:
    return {
        "assets": {
            "resourcepacks": {
                "example": {
                    "provider": "modrinth",
                    "slug": "example-pack",
                    "type": "resourcepack",
                }
            },
            "shaderpacks": {},
        },
        "asset_sets": {"example_set": {"resourcepacks": ["example"]}},
    }


def _config_with_patterns(patterns: object) -> dict:
    config = _asset_config()
    config["assets"]["resourcepacks"]["example"]["file_patterns"] = patterns
    return config


def test_install_asset_set_caches_legacy_source_and_records_eligibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = tmp_path / "resourcepacks" / "example.zip"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"upstream bytes")
    monkeypatch.setattr(
        packs,
        "latest_project_version",
        lambda *_args, **_kwargs: _version(game_versions=["26.2"]),
    )

    resourcepacks, shaderpack = packs.install_asset_set(
        tmp_path,
        game_version="26.2",
        asset_config=_asset_config(),
        asset_set_name="example_set",
    )

    source = tmp_path / ".mcdata" / "resourcepack_sources" / "example.zip"
    receipt = packs.load_resourcepack_sources(tmp_path)
    assert resourcepacks == ["example.zip"]
    assert shaderpack is None
    assert source.read_bytes() == b"upstream bytes"
    assert receipt["target_game_version"] == "26.2"
    assert receipt["resourcepacks"][0]["project"] == "example-pack"
    assert receipt["resourcepacks"][0]["game_versions"] == ["26.2"]


def test_install_asset_set_downloads_into_source_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        packs,
        "latest_project_version",
        lambda *_args, **_kwargs: _version(game_versions=["26.2"], payload=b"downloaded"),
    )

    def fake_download(_url: str, dest: Path) -> None:
        dest.write_bytes(b"downloaded")

    monkeypatch.setattr(packs, "download_file", fake_download)

    packs.install_asset_set(
        tmp_path,
        game_version="26.2",
        asset_config=_asset_config(),
        asset_set_name="example_set",
    )

    assert (
        tmp_path / ".mcdata" / "resourcepack_sources" / "example.zip"
    ).read_bytes() == b"downloaded"
    assert not (tmp_path / "resourcepacks" / "example.zip").exists()


def test_install_asset_set_defaults_to_primary_when_version_has_multiple_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version, payloads = _multi_file_version()
    downloaded: list[str] = []
    monkeypatch.setattr(packs, "latest_project_version", lambda *_args, **_kwargs: version)

    def fake_download(url: str, dest: Path) -> None:
        downloaded.append(dest.name)
        dest.write_bytes(payloads[dest.name])

    monkeypatch.setattr(packs, "download_file", fake_download)

    resourcepacks, _shaderpack = packs.install_asset_set(
        tmp_path,
        game_version="26.2",
        asset_config=_asset_config(),
        asset_set_name="example_set",
    )

    receipt = packs.load_resourcepack_sources(tmp_path)["resourcepacks"]
    assert resourcepacks == ["Patrix_26.2_32x_basic.zip"]
    assert downloaded == resourcepacks
    assert [record["filename"] for record in receipt] == resourcepacks
    assert "selection_pattern" not in receipt[0]


def test_install_asset_set_selects_multiple_version_files_in_config_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version, payloads = _multi_file_version()
    version_calls: list[str] = []

    def fake_version(slug: str, **_kwargs) -> ProjectVersion:
        version_calls.append(slug)
        return version

    def fake_download(_url: str, dest: Path) -> None:
        dest.write_bytes(payloads[dest.name])

    monkeypatch.setattr(packs, "latest_project_version", fake_version)
    monkeypatch.setattr(packs, "download_file", fake_download)
    patterns = [
        "Patrix_*_models.zip",
        "Patrix_26.2_32x_basic.zip",
        "Patrix_*_32x_addon.zip",
    ]

    resourcepacks, _shaderpack = packs.install_asset_set(
        tmp_path,
        game_version="26.2",
        asset_config=_config_with_patterns(patterns),
        asset_set_name="example_set",
    )

    expected = [
        "Patrix_26.2_models.zip",
        "Patrix_26.2_32x_basic.zip",
        "Patrix_26.2_32x_addon.zip",
    ]
    receipt = packs.load_resourcepack_sources(tmp_path)["resourcepacks"]
    assert version_calls == ["example-pack"]
    assert resourcepacks == expected
    assert [record["filename"] for record in receipt] == expected
    assert [record["selection_pattern"] for record in receipt] == patterns
    assert all(record["version"] == "89" for record in receipt)
    assert all(record["project"] == "example-pack" for record in receipt)
    assert all(record["expected_sha512"] for record in receipt)


def test_install_asset_set_treats_an_exact_filename_with_glob_characters_literally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"bracketed filename"
    version = _version(
        game_versions=["26.2"],
        payload=payload,
        filename="NewGlowingOres-[Border].zip",
    )
    monkeypatch.setattr(packs, "latest_project_version", lambda *_args, **_kwargs: version)
    monkeypatch.setattr(
        packs,
        "download_file",
        lambda _url, dest: dest.write_bytes(payload),
    )

    resourcepacks, _shaderpack = packs.install_asset_set(
        tmp_path,
        game_version="26.2",
        asset_config=_config_with_patterns(["NewGlowingOres-[Border].zip"]),
        asset_set_name="example_set",
    )

    assert resourcepacks == ["NewGlowingOres-[Border].zip"]


@pytest.mark.parametrize(
    ("patterns", "message"),
    [
        (["Patrix_*_missing.zip"], "matched no files"),
        (["Patrix_26.2_32x_*.zip"], "matched 3 files"),
        (
            ["Patrix_*_32x_basic.zip", "Patrix_26.2_32x_basic.zip"],
            "Duplicate selected resource-pack filename",
        ),
    ],
)
def test_install_asset_set_rejects_missing_ambiguous_or_duplicate_pattern_before_download(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    patterns: list[str],
    message: str,
) -> None:
    version, _payloads = _multi_file_version()
    monkeypatch.setattr(packs, "latest_project_version", lambda *_args, **_kwargs: version)
    monkeypatch.setattr(
        packs,
        "download_file",
        lambda *_args, **_kwargs: pytest.fail("invalid selection must fail before download"),
    )

    with pytest.raises(RuntimeError, match=message):
        packs.install_asset_set(
            tmp_path,
            game_version="26.2",
            asset_config=_config_with_patterns(patterns),
            asset_set_name="example_set",
        )


@pytest.mark.parametrize(
    "patterns",
    [
        [],
        "Patrix_*.zip",
        ["../Patrix_*.zip"],
        ["nested/Patrix_*.zip"],
        ["Patrix_*.jar"],
        ["Patrix_*,bonus.zip"],
        ["Patrix_*\n.zip"],
    ],
)
def test_install_asset_set_rejects_invalid_file_patterns_before_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, patterns: object
) -> None:
    version, _payloads = _multi_file_version()
    monkeypatch.setattr(packs, "latest_project_version", lambda *_args, **_kwargs: version)
    monkeypatch.setattr(
        packs,
        "download_file",
        lambda *_args, **_kwargs: pytest.fail("unsafe pattern must fail before download"),
    )

    with pytest.raises(RuntimeError, match="file_patterns|Unsafe Modrinth file pattern"):
        packs.install_asset_set(
            tmp_path,
            game_version="26.2",
            asset_config=_config_with_patterns(patterns),
            asset_set_name="example_set",
        )


def test_install_asset_set_rejects_cross_asset_filename_collision_before_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _asset_config()
    config["assets"]["resourcepacks"]["alias"] = {
        "provider": "modrinth",
        "slug": "alias-pack",
        "type": "resourcepack",
    }
    config["asset_sets"]["example_set"]["resourcepacks"] = ["example", "alias"]
    monkeypatch.setattr(
        packs,
        "latest_project_version",
        lambda *_args, **_kwargs: _version(game_versions=["26.2"]),
    )
    monkeypatch.setattr(
        packs,
        "download_file",
        lambda *_args, **_kwargs: pytest.fail("duplicate names must fail before download"),
    )

    with pytest.raises(RuntimeError, match="Duplicate selected resource-pack filename"):
        packs.install_asset_set(
            tmp_path,
            game_version="26.2",
            asset_config=config,
            asset_set_name="example_set",
        )


def test_install_asset_set_replaces_wrong_same_name_source_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / ".mcdata" / "resourcepack_sources" / "example.zip"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"stale bytes with reused filename")
    monkeypatch.setattr(
        packs,
        "latest_project_version",
        lambda *_args, **_kwargs: _version(game_versions=["26.2"], payload=b"current bytes"),
    )

    def fake_download(_url: str, dest: Path) -> None:
        dest.write_bytes(b"current bytes")

    monkeypatch.setattr(packs, "download_file", fake_download)

    packs.install_asset_set(
        tmp_path,
        game_version="26.2",
        asset_config=_asset_config(),
        asset_set_name="example_set",
    )

    assert source.read_bytes() == b"current bytes"


def test_install_asset_set_rejects_version_without_target_declaration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        packs,
        "latest_project_version",
        lambda *_args, **_kwargs: _version(game_versions=["1.21.8"]),
    )

    with pytest.raises(RuntimeError, match="does not declare target Minecraft version 26.2"):
        packs.install_asset_set(
            tmp_path,
            game_version="26.2",
            asset_config=_asset_config(),
            asset_set_name="example_set",
        )


def test_install_asset_set_rejects_traversal_filename_before_filesystem_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "outside.zip"
    outside.write_bytes(b"must remain unchanged")
    monkeypatch.setattr(
        packs,
        "latest_project_version",
        lambda *_args, **_kwargs: _version(
            game_versions=["26.2"],
            filename="../../outside.zip",
        ),
    )
    monkeypatch.setattr(
        packs,
        "download_file",
        lambda *_args, **_kwargs: pytest.fail("unsafe filename must fail before download"),
    )

    with pytest.raises(RuntimeError, match="Unsafe Modrinth resource-pack filename"):
        packs.install_asset_set(
            tmp_path,
            game_version="26.2",
            asset_config=_asset_config(),
            asset_set_name="example_set",
        )

    assert outside.read_bytes() == b"must remain unchanged"


def test_install_asset_set_writes_empty_source_manifest(tmp_path: Path) -> None:
    resourcepacks, shaderpack = packs.install_asset_set(
        tmp_path,
        game_version="26.2",
        asset_config={
            "assets": {"resourcepacks": {}, "shaderpacks": {}},
            "asset_sets": {"vanilla": {"resourcepacks": []}},
        },
        asset_set_name="vanilla",
    )

    assert resourcepacks == []
    assert shaderpack is None
    receipt_path = tmp_path / ".mcdata" / packs.RESOURCEPACK_SOURCE_MANIFEST
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["resourcepacks"] == []


@pytest.mark.parametrize(
    "payload",
    ["not-json", "{}", '{"schema_version": 1, "target_game_version": "26.2"}'],
)
def test_load_resourcepack_sources_rejects_invalid_receipt(tmp_path: Path, payload: str) -> None:
    receipt = tmp_path / ".mcdata" / packs.RESOURCEPACK_SOURCE_MANIFEST
    receipt.parent.mkdir()
    receipt.write_text(payload, encoding="utf-8")

    with pytest.raises(RuntimeError, match="resource-pack source manifest"):
        packs.load_resourcepack_sources(tmp_path)
