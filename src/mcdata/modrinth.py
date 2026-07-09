from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from .net import get_json


API = "https://api.modrinth.com/v2"


@dataclass(frozen=True)
class VersionFile:
    filename: str
    url: str
    primary: bool
    sha512: str | None = None
    sha1: str | None = None
    size: int | None = None


@dataclass(frozen=True)
class ProjectVersion:
    project: str
    version_number: str
    version_type: str
    game_versions: list[str]
    loaders: list[str]
    files: list[VersionFile]

    @property
    def primary_file(self) -> VersionFile:
        for file in self.files:
            if file.primary:
                return file
        if not self.files:
            raise RuntimeError(f"Modrinth version has no files: {self.project} {self.version_number}")
        return self.files[0]


def project_versions(
    slug: str,
    *,
    game_version: str | None = None,
    loaders: list[str] | None = None,
) -> list[ProjectVersion]:
    params: dict[str, str] = {}
    if game_version:
        params["game_versions"] = f'["{game_version}"]'
    if loaders:
        params["loaders"] = "[" + ",".join(f'"{loader}"' for loader in loaders) + "]"
    url = f"{API}/project/{slug}/version"
    if params:
        url += "?" + urlencode(params)
    data = get_json(url)
    if not isinstance(data, list):
        raise RuntimeError(f"Unexpected Modrinth response for {slug}")
    return [_parse_version(slug, item) for item in data]


def latest_project_version(
    slug: str,
    *,
    game_version: str | None = None,
    loaders: list[str] | None = None,
    version_types: list[str] | None = None,
) -> ProjectVersion:
    versions = project_versions(slug, game_version=game_version, loaders=loaders)
    if version_types:
        allowed = set(version_types)
        versions = [version for version in versions if version.version_type in allowed]
    if not versions:
        raise RuntimeError(
            f"No Modrinth versions for {slug} game={game_version} loaders={loaders} "
            f"version_types={version_types}"
        )
    return versions[0]


def _parse_version(slug: str, item: dict) -> ProjectVersion:
    files = [
        VersionFile(
            filename=str(f["filename"]),
            url=str(f["url"]),
            primary=bool(f.get("primary", False)),
            sha512=_optional_hash(f, "sha512"),
            sha1=_optional_hash(f, "sha1"),
            size=int(f["size"]) if isinstance(f.get("size"), int) else None,
        )
        for f in item.get("files", [])
    ]
    return ProjectVersion(
        project=slug,
        version_number=str(item["version_number"]),
        version_type=str(item.get("version_type", "release")),
        game_versions=[str(v) for v in item.get("game_versions", [])],
        loaders=[str(v) for v in item.get("loaders", [])],
        files=files,
    )


def _optional_hash(file_data: dict, algorithm: str) -> str | None:
    hashes = file_data.get("hashes")
    if not isinstance(hashes, dict):
        return None
    value = hashes.get(algorithm)
    return str(value) if isinstance(value, str) and value else None
