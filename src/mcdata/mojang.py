from __future__ import annotations

from dataclasses import dataclass

from .net import get_json


VERSION_MANIFEST = "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json"


@dataclass(frozen=True)
class MojangVersion:
    id: str
    type: str
    release_time: str


def version_manifest() -> dict:
    data = get_json(VERSION_MANIFEST)
    if not isinstance(data, dict):
        raise RuntimeError("Unexpected Mojang manifest response")
    return data


def latest_release() -> str:
    return str(version_manifest()["latest"]["release"])


def release_versions(limit: int = 80) -> list[MojangVersion]:
    data = version_manifest()
    out: list[MojangVersion] = []
    for item in data.get("versions", []):
        if item.get("type") != "release":
            continue
        out.append(
            MojangVersion(
                id=str(item["id"]),
                type=str(item["type"]),
                release_time=str(item["releaseTime"]),
            )
        )
        if len(out) >= limit:
            break
    return out

