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
        lambda *_args, **_kwargs: _version(
            game_versions=["26.2"], payload=b"downloaded"
        ),
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


def test_install_asset_set_replaces_wrong_same_name_source_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / ".mcdata" / "resourcepack_sources" / "example.zip"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"stale bytes with reused filename")
    monkeypatch.setattr(
        packs,
        "latest_project_version",
        lambda *_args, **_kwargs: _version(
            game_versions=["26.2"], payload=b"current bytes"
        ),
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
def test_load_resourcepack_sources_rejects_invalid_receipt(
    tmp_path: Path, payload: str
) -> None:
    receipt = tmp_path / ".mcdata" / packs.RESOURCEPACK_SOURCE_MANIFEST
    receipt.parent.mkdir()
    receipt.write_text(payload, encoding="utf-8")

    with pytest.raises(RuntimeError, match="resource-pack source manifest"):
        packs.load_resourcepack_sources(tmp_path)
