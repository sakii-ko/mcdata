import json
from pathlib import Path

from mcdata.render import pipeline
from mcdata.render.probe import (
    parse_position_log,
    parse_rotation_log,
    replay_start_mono_from_log,
    wait_for_position_sample,
    write_positions_jsonl,
)


def test_position_log_is_parsed_to_jsonl(tmp_path: Path) -> None:
    log = tmp_path / "server.log"
    log.write_text(
        "[Server thread/INFO]: mcdata_bot has the following entity data: [1.5d, 64.0d, -2.25d]\n"
        "[Server thread/INFO]: mcdata_bot has the following entity data: [-90.0f, 15.0f]\n"
        "[Server thread/INFO]: mcdata_bot has the following entity data: [2.0d, 65.0d, -3.0d]\n",
        encoding="utf-8",
    )
    out = tmp_path / "positions.jsonl"

    count = write_positions_jsonl(
        log,
        out,
        username="mcdata_bot",
        sent_at=[9.0, 14.5],
        replay_start_mono=10.0,
    )

    assert count == 2
    assert parse_position_log(log, username="mcdata_bot") == [
        {"x": 1.5, "y": 64.0, "z": -2.25},
        {"x": 2.0, "y": 65.0, "z": -3.0},
    ]
    assert parse_rotation_log(log, username="mcdata_bot") == [{"yaw": -90.0}]
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert rows == [
        {"idx": 0, "t_rel": -1.0, "x": 1.5, "y": 64.0, "yaw": -90.0, "z": -2.25},
        {"idx": 1, "t_rel": 4.5, "x": 2.0, "y": 65.0, "z": -3.0},
    ]


def test_position_jsonl_prefers_replay_log_start(tmp_path: Path) -> None:
    log = tmp_path / "server.log"
    log.write_text(
        "[Server thread/INFO]: mcdata_bot has the following entity data: [1.5d, 64.0d, -2.25d]\n",
        encoding="utf-8",
    )
    replay_log = tmp_path / "replay_log.jsonl"
    replay_log.write_text(json.dumps({"event": "start", "mono": 12.0}) + "\n", encoding="utf-8")
    out = tmp_path / "positions.jsonl"

    count = write_positions_jsonl(
        log,
        out,
        username="mcdata_bot",
        sent_at=[13.25],
        replay_start_mono=10.0,
        replay_log_path=replay_log,
    )

    assert count == 1
    assert replay_start_mono_from_log(replay_log) == 12.0
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["t_rel"] == 1.25


def test_wait_for_position_sample_returns_after_new_sample(tmp_path: Path) -> None:
    log = tmp_path / "server.log"
    log.write_text(
        "[Server thread/INFO]: mcdata_bot has the following entity data: [1.5d, 64.0d, -2.25d]\n",
        encoding="utf-8",
    )

    count = wait_for_position_sample(log, "mcdata_bot", after_count=0, wait_sec=0.1)

    assert count == 1


def test_capture_reapplies_state_and_writes_positions(tmp_path: Path, monkeypatch) -> None:
    events: list[tuple[str, object | None]] = []
    _patch_capture_pipeline(tmp_path, monkeypatch, events)

    pipeline.launch_profile(
        tmp_path,
        "matrix_low",
        dry_run=False,
        capture=True,
        strategy=None,
        duration=1,
        with_server=True,
        replay_actions=False,
        trajectory_path=None,
        game_version="26.2",
        probe_interval=1.25,
    )

    assert events[:5] == [
        ("join", None),
        ("apply_join_state", None),
        ("resourcepacks_verified", None),
        ("apply_join_state", None),
        ("prepare_view", None),
    ]
    assert ("probe_start", 1.25) in events
    assert events.index(("probe_first_sample", None)) < events.index(("wait_capture", None))
    assert ("probe_stop", None) in events
    assert ("positions_written", None) in events
    run_dir = next((tmp_path / "runs").glob("*_matrix_low"))
    pipeline_events = [
        json.loads(line)
        for line in (run_dir / "pipeline.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(item["stage"] == "join" and item["event"] == "re_apply_state" for item in pipeline_events)
    assert any(item["stage"] == "replay" and item["event"] == "released" for item in pipeline_events)
    assert any(
        item["stage"] == "position_probe" and item["event"] == "positions_written" and item["count"] == 2
        for item in pipeline_events
    )
    assert any(
        item["stage"] == "position_probe" and item["event"] == "start" and item["interval_sec"] == 1.25
        for item in pipeline_events
    )
    assert any(
        item["stage"] == "server" and item["event"] == "scene_verified" and item["receipt_count"] == 2
        for item in pipeline_events
    )
    assert any(
        item["stage"] == "capture"
        and item["event"] == "window_geometry"
        and item["geometry"] == {"x": 12, "y": 34, "width": 320, "height": 180}
        for item in pipeline_events
    )


def test_capture_debug_flags_skip_reapply_and_replay_gate(tmp_path: Path, monkeypatch) -> None:
    events: list[tuple[str, object | None]] = []
    _patch_capture_pipeline(tmp_path, monkeypatch, events)

    pipeline.launch_profile(
        tmp_path,
        "matrix_low",
        dry_run=False,
        capture=True,
        strategy=None,
        duration=1,
        with_server=True,
        replay_actions=False,
        trajectory_path=None,
        game_version="26.2",
        debug_no_reapply=True,
        debug_no_replay_gate=True,
    )

    assert events.count(("apply_join_state", None)) == 1
    assert ("probe_start", 5.0) in events
    assert ("probe_first_sample", None) not in events
    run_dir = next((tmp_path / "runs").glob("*_matrix_low"))
    pipeline_events = [
        json.loads(line)
        for line in (run_dir / "pipeline.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(
        item["stage"] == "join" and item["event"] == "re_apply_state_skipped"
        for item in pipeline_events
    )
    assert any(
        item["stage"] == "position_probe" and item["event"] == "first_sample_skipped"
        for item in pipeline_events
    )


def _patch_capture_pipeline(tmp_path: Path, monkeypatch, events: list[tuple[str, object | None]]) -> None:
    profile = {
        "loader": "fabric",
        "quality": "low",
        "asset_set": "vanilla",
        "width": 320,
        "height": 180,
        "username": "mcdata_bot",
        "jvm_args": "-Xmx1G",
        "server_port": 25570,
        "world_seed": 1,
        "world_profile": "render_matrix_base",
        "world_state": {},
        "capture_ready_delay_sec": 0,
    }

    class FakeProc:
        args = ["fake"]
        returncode = 0
        pid = 123

        def poll(self):
            return None

    class FakeStop:
        def set(self) -> None:
            events.append(("probe_stop", None))

    class FakeResourcePackRecord:
        def to_dict(self) -> dict[str, object]:
            return {"status": "pass", "expected_file_packs": [], "actual_file_packs": []}

    monkeypatch.setenv("MCDATA_WORK_DIR", str(tmp_path / "instances"))
    monkeypatch.setenv("MCDATA_OUTPUT_DIR", str(tmp_path / "runs"))
    (tmp_path / "instances" / "matrix_low").mkdir(parents=True)
    _write_empty_scene_config(tmp_path)
    monkeypatch.setattr(pipeline, "load_profile", lambda _configs, _name: dict(profile))
    monkeypatch.setattr(pipeline, "_start_replay_thread", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline, "start_server", lambda *args, **kwargs: FakeProc())
    monkeypatch.setattr(pipeline, "expected_scene_fill_count", lambda _profile: 2)
    monkeypatch.setattr(pipeline, "verify_scene_commands", lambda *args, **kwargs: 2)
    monkeypatch.setattr(pipeline, "wait_for_player_join", lambda *args, **kwargs: events.append(("join", None)))
    monkeypatch.setattr(pipeline, "apply_join_state", lambda _proc, _profile: events.append(("apply_join_state", None)))
    monkeypatch.setattr(
        pipeline,
        "validate_resourcepack_runtime",
        lambda *_args, **_kwargs: events.append(("resourcepacks_verified", None))
        or FakeResourcePackRecord(),
    )
    monkeypatch.setattr(pipeline, "_prepare_capture_view", lambda _settings: events.append(("prepare_view", None)))
    monkeypatch.setattr(pipeline, "_minecraft_window_geometry", lambda window_name="Minecraft": (12, 34, 320, 180))
    monkeypatch.setattr(pipeline, "_start_capture", lambda *args, **kwargs: (FakeProc(), ["ffmpeg"]))
    monkeypatch.setattr(pipeline, "_wait_for_capture", lambda *args, **kwargs: events.append(("wait_capture", None)))
    monkeypatch.setattr(
        pipeline,
        "start_position_probe",
        lambda *args, **kwargs: events.append(("probe_start", kwargs.get("interval_sec"))) or FakeStop(),
    )
    monkeypatch.setattr(
        pipeline,
        "wait_for_position_sample",
        lambda *args, **kwargs: events.append(("probe_first_sample", None)) or 1,
    )
    monkeypatch.setattr(
        pipeline,
        "write_positions_jsonl",
        lambda *args, **kwargs: events.append(("positions_written", None)) or 2,
    )
    monkeypatch.setattr(pipeline, "_terminate_process_tree", lambda proc, *, timeout: events.append(("terminate", timeout)))
    monkeypatch.setattr(pipeline, "_resource_manifest", lambda _work_dir, _profile: {
        "asset_set": "vanilla",
        "mods": [],
        "resourcepacks": [],
        "resourcepack_resolution": {"packs": []},
        "resourcepack_runtime": None,
        "shaderpacks": [],
    })
    monkeypatch.setattr(pipeline, "_env_manifest", lambda *, display: {"hostname": "host", "display": display})
    monkeypatch.setattr(pipeline, "_git_manifest", lambda _root: {"commit": "abc", "dirty": False})
    monkeypatch.setattr(pipeline.subprocess, "Popen", lambda *args, **kwargs: FakeProc())


def _write_empty_scene_config(root: Path) -> None:
    config_dir = root / "configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "scene.yml").write_text(
        "scene:\n  origin: [0, 64, 0]\n  entries: []\n",
        encoding="utf-8",
    )
