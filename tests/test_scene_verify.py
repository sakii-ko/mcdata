from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from mcdata.render.scene import (
    apply_join_state,
    apply_world_state,
    expected_scene_fill_count,
    verify_scene_commands,
)
from mcdata.scene_model import load_scene, scene_mapping

ROOT = Path(__file__).resolve().parents[1]


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


def test_verify_scene_commands_raises_on_missing_receipt(tmp_path: Path) -> None:
    log = tmp_path / "server.log"
    log.write_text("[Server thread/INFO]: Successfully filled 1 block\n", encoding="utf-8")

    with pytest.raises(TimeoutError, match="1/2"):
        verify_scene_commands(log, expected_fill_count=2, wait_sec=0.01, poll_sec=0.001)


def test_expected_scene_fill_count_excludes_forceload() -> None:
    profile = {"world_state": {"scene": scene_mapping(load_scene(ROOT / "configs"))}}

    assert expected_scene_fill_count(profile) == 24


def test_apply_world_state_freezes_ticks_and_clears_stale_item_entities() -> None:
    proc = SimpleNamespace(stdin=StringIO())
    profile = {
        "world_state": {
            "gamerules": {"random_tick_speed": 0},
            "time": "noon",
            "weather": "clear",
            "weather_duration_sec": 60,
            "scene": {"enabled": False},
            "clear_dropped_items": True,
        }
    }

    apply_world_state(proc, profile)

    assert proc.stdin.getvalue().splitlines() == [
        "gamerule random_tick_speed 0",
        "time set noon",
        "weather clear 60",
        "kill @e[type=minecraft:item]",
    ]


def test_apply_join_state_pregrants_recipes_before_capture_warmup() -> None:
    proc = SimpleNamespace(stdin=StringIO())
    profile = {
        "world_state": {
            "time": "midnight",
            "pregrant_recipes": True,
            "player": {"x": 1, "y": 64, "z": -2, "yaw": 90, "pitch": 18},
        }
    }

    apply_join_state(proc, profile)

    assert proc.stdin.getvalue().splitlines() == [
        "time set midnight",
        "recipe give @a *",
        "tp @a 1 64 -2 90 18",
    ]
