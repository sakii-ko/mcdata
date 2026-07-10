from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from mcdata.config import load_profile
from mcdata.render.scene import (
    _biome_commands,
    apply_join_state,
    apply_world_state,
    expected_scene_fill_count,
    verify_scene_commands,
)
from mcdata.scene_model import load_scene, scene_mapping

ROOT = Path(__file__).resolve().parents[1]


def _snow_profile_with_scene() -> dict:
    profile = load_profile(ROOT / "configs", "lookdev_pair_legendary_unbound_snow_1080p")
    profile["world_state"]["scene"] = scene_mapping(load_scene(ROOT / "configs"))
    return profile


def _mutation_receipts(*, biome_count: int) -> str:
    scene = ["[Server thread/INFO]: Successfully filled 1 block\n"] * 126
    biome = ["[Server thread/INFO]: Biomes set between -32, 60, -32 and 31, 67, 31\n"] + [
            "[Server thread/INFO]: 32768 biome entry/entries set between "
            "-32, 68, -32 and 31, 75, 31\n"
    ] * 4
    return "".join([*scene, *biome[:biome_count]])


def test_verify_scene_commands_accepts_matching_receipts(tmp_path: Path) -> None:
    log = tmp_path / "server.log"
    log.write_text(
        "[Server thread/INFO]: Successfully filled 31487 blocks\n"
        "[Server thread/INFO]: Changed the block\n"
        "[Server thread/INFO]: No blocks were filled\n",
        encoding="utf-8",
    )

    assert verify_scene_commands(log, expected_fill_count=3, wait_sec=0.01, poll_sec=0.001) == 3


def test_verify_scene_commands_raises_on_overlimit_receipt(tmp_path: Path) -> None:
    log = tmp_path / "server.log"
    log.write_text(
        "[Server thread/ERROR]: Too many blocks in the specified area (39701 > 32768)\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Too many blocks.*server.log"):
        verify_scene_commands(log, expected_fill_count=1, wait_sec=0.01, poll_sec=0.001)


def test_verify_scene_commands_raises_on_invalid_command(tmp_path: Path) -> None:
    log = tmp_path / "server.log"
    log.write_text(
        "[Server thread/INFO]: Incorrect argument for command\n"
        "[Server thread/INFO]: gamerule invalid_name false<--[HERE]\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Incorrect argument.*server.log"):
        verify_scene_commands(log, expected_fill_count=0, wait_sec=0.01, poll_sec=0.001)


def test_verify_scene_commands_raises_on_missing_receipt(tmp_path: Path) -> None:
    log = tmp_path / "server.log"
    log.write_text("[Server thread/INFO]: Successfully filled 1 block\n", encoding="utf-8")

    with pytest.raises(TimeoutError, match="1/2"):
        verify_scene_commands(log, expected_fill_count=2, wait_sec=0.01, poll_sec=0.001)


def test_expected_scene_fill_count_excludes_forceload() -> None:
    profile = {"world_state": {"scene": scene_mapping(load_scene(ROOT / "configs"))}}

    assert expected_scene_fill_count(profile) == 126


def test_snow_profile_gate_accepts_all_scene_and_biome_receipts(tmp_path: Path) -> None:
    log = tmp_path / "server.log"
    log.write_text(_mutation_receipts(biome_count=5), encoding="utf-8")
    expected_count = expected_scene_fill_count(_snow_profile_with_scene())

    assert expected_count == 126 + 5
    assert (
        verify_scene_commands(
            log,
            expected_fill_count=expected_count,
            wait_sec=0.01,
            poll_sec=0.001,
        )
        == expected_count
    )


def test_snow_profile_gate_waits_for_every_biome_receipt(tmp_path: Path) -> None:
    log = tmp_path / "server.log"
    log.write_text(_mutation_receipts(biome_count=4), encoding="utf-8")
    expected_count = expected_scene_fill_count(_snow_profile_with_scene())

    with pytest.raises(TimeoutError, match="130/131"):
        verify_scene_commands(
            log,
            expected_fill_count=expected_count,
            wait_sec=0.01,
            poll_sec=0.001,
        )


def test_apply_world_state_freezes_ticks_and_clears_stale_entities() -> None:
    proc = SimpleNamespace(stdin=StringIO())
    profile = {
        "world_state": {
            "gamerules": {"random_tick_speed": 0},
            "time": "noon",
            "weather": "clear",
            "weather_duration_sec": 60,
            "scene": {
                "enabled": True,
                "origin": [0, 64, 0],
                "entries": [
                    {"kind": "setblock", "block": "minecraft:stone", "at": [1, 0, 2]}
                ],
            },
            "clear_non_player_entities": True,
            "clear_dropped_items": True,
        }
    }

    apply_world_state(proc, profile)

    assert proc.stdin.getvalue().splitlines() == [
        "gamerule random_tick_speed 0",
        "time set noon",
        "weather clear 60",
        "setblock 1 64 2 minecraft:stone",
        "kill @e[type=!minecraft:player]",
        "kill @e[type=minecraft:item]",
    ]


def test_apply_join_state_pregrants_recipes_before_capture_warmup() -> None:
    proc = SimpleNamespace(stdin=StringIO())
    profile = {
        "world_state": {
            "time": "midnight",
            "clear_inventory": True,
            "pregrant_recipes": True,
            "player": {"x": 1, "y": 64, "z": -2, "yaw": 90, "pitch": 18},
        }
    }

    apply_join_state(proc, profile)

    assert proc.stdin.getvalue().splitlines() == [
        "time set midnight",
        "clear @a",
        "recipe give @a *",
        "tp @a 1 64 -2 90 18",
    ]


def test_apply_world_state_sets_numeric_time_and_controlled_biome_regions() -> None:
    proc = SimpleNamespace(stdin=StringIO())
    profile = {
        "world_state": {
            "time": 12000,
            "weather": "rain",
            "biome": {
                "id": "minecraft:snowy_plains",
                "precipitation": "snow",
                "regions": [
                    {"from": [-32, 60, -32], "to": [31, 67, 31]},
                    {"from": [-32, 68, -32], "to": [31, 75, 31]},
                ],
            },
        }
    }

    apply_world_state(proc, profile)

    assert proc.stdin.getvalue().splitlines() == [
        "time set 12000",
        "weather rain 999999",
        "fillbiome -32 60 -32 31 67 31 minecraft:snowy_plains",
        "fillbiome -32 68 -32 31 75 31 minecraft:snowy_plains",
    ]


def test_biome_command_rejects_region_over_server_limit() -> None:
    with pytest.raises(ValueError, match="volume 65536 exceeds 32768"):
        _biome_commands(
            {
                "id": "minecraft:snowy_plains",
                "precipitation": "snow",
                "regions": [{"from": [-32, 60, -32], "to": [31, 75, 31]}],
            }
        )
