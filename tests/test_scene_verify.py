from pathlib import Path

import pytest

from mcdata.render.scene import expected_scene_fill_count, verify_scene_commands
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
