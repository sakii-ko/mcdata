import json
import os
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from mcdata.render import pipeline
from mcdata.render.pipeline import _copy_trajectory, _profile_with_overrides, _run_dir
from mcdata.render.server import (
    ensure_server,
    expected_scene_fill_count,
    parse_position_log,
    parse_rotation_log,
    replay_start_mono_from_log,
    server_profile_name,
    verify_scene_commands,
    wait_for_position_sample,
    write_positions_jsonl,
)


def test_copy_trajectory_uses_final_path_without_tmp_leftover(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"events": [{"t": 0.0}]}) + "\n", encoding="utf-8")
    run_dir = tmp_path / "run"

    copied = _copy_trajectory(run_dir, source)

    assert copied == run_dir / "trajectory.json"
    assert copied.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    assert not (run_dir / "trajectory.json.tmp").exists()


def test_run_dir_includes_lane_suffix(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path, "matrix_low", lane="gpu1")

    assert run_dir.name.endswith("_matrix_low__gpu1")
    assert run_dir.exists()


def test_server_port_overlay_replaces_profile_port() -> None:
    profile = {"server_port": 25570, "quality": "low"}

    overlaid = _profile_with_overrides(profile, server_port=25602)

    assert overlaid["server_port"] == 25602
    assert profile["server_port"] == 25570


def test_server_lane_isolates_world_directory_and_level_name(tmp_path: Path) -> None:
    server_root = tmp_path / "servers"
    launcher_dir = tmp_path / "launcher"
    cached = server_root / "cache" / "minecraft_server.26.2.jar"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"jar")
    (launcher_dir / "jvm" / "runtime" / "bin").mkdir(parents=True)
    (launcher_dir / "jvm" / "runtime" / "bin" / "java").write_text("#!/bin/sh\n", encoding="utf-8")

    profile = {"world_profile": "render_matrix_base", "server_port": 25603}
    info = ensure_server(
        server_root,
        launcher_dir,
        game_version="26.2",
        profile_name="matrix_low",
        profile=profile,
        lane="gpu3",
    )

    assert info["server_dir"] == server_root / "render_matrix_base__gpu3"
    properties = (info["server_dir"] / "server.properties").read_text(encoding="utf-8")
    assert "level-name=render_matrix_base__gpu3\n" in properties
    assert "server-port=25603\n" in properties
    assert server_profile_name(profile, profile_name="matrix_low", lane="gpu3") == "render_matrix_base__gpu3"


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
    profile = {"world_state": {"scene": {"enabled": True, "origin": [0, 64, 0]}}}

    assert expected_scene_fill_count(profile) == 24


def test_wait_for_position_sample_returns_after_new_sample(tmp_path: Path) -> None:
    log = tmp_path / "server.log"
    log.write_text(
        "[Server thread/INFO]: mcdata_bot has the following entity data: [1.5d, 64.0d, -2.25d]\n",
        encoding="utf-8",
    )

    count = wait_for_position_sample(log, "mcdata_bot", after_count=0, wait_sec=0.1)

    assert count == 1


def test_parallel_dry_run_lanes_write_independent_manifests(tmp_path: Path, monkeypatch) -> None:
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
    }

    monkeypatch.setenv("MCDATA_WORK_DIR", str(tmp_path / "instances"))
    monkeypatch.setenv("MCDATA_OUTPUT_DIR", str(tmp_path / "runs"))
    (tmp_path / "instances" / "matrix_low").mkdir(parents=True)
    monkeypatch.setattr(pipeline, "load_profile", lambda _configs, _name: dict(profile))
    monkeypatch.setattr(pipeline, "resolve_game_version", lambda _profile: "26.2")
    monkeypatch.setattr(pipeline, "_resource_manifest", lambda _work_dir, _profile: {
        "asset_set": _profile.get("asset_set", "vanilla"),
        "mods": [],
        "resourcepacks": [],
        "shaderpacks": [],
    })
    monkeypatch.setattr(pipeline, "_env_manifest", lambda *, display: {
        "hostname": "host",
        "display": display,
        "platform": "test",
        "python": "3",
        "gl_renderer": None,
        "gpu": [],
    })
    monkeypatch.setattr(pipeline, "_git_manifest", lambda _root: {"commit": "abc", "dirty": False})

    def fake_run(_cmd, **_kwargs):
        return subprocess.CompletedProcess(_cmd, 0)

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)

    def launch(lane: str, port: int) -> Path:
        pipeline.launch_profile(
            tmp_path,
            "matrix_low",
            dry_run=True,
            capture=False,
            strategy=None,
            duration=1,
            with_server=True,
            replay_actions=False,
            trajectory_path=None,
            game_version="26.2",
            server_port=port,
            lane=lane,
        )
        matches = sorted((tmp_path / "runs").glob(f"*_matrix_low__{lane}"))
        assert len(matches) == 1
        return matches[0]

    with ThreadPoolExecutor(max_workers=2) as pool:
        run_a, run_b = pool.map(lambda item: launch(*item), [("gpu0", 25600), ("gpu1", 25601)])

    manifest_a = json.loads((run_a / "manifest.json").read_text(encoding="utf-8"))
    manifest_b = json.loads((run_b / "manifest.json").read_text(encoding="utf-8"))
    assert manifest_a["lane"] == "gpu0"
    assert manifest_b["lane"] == "gpu1"
    assert manifest_a["profile"]["server_port"] == 25600
    assert manifest_b["profile"]["server_port"] == 25601
    assert run_a != run_b


def test_git_manifest_falls_back_to_sync_commit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "_git_output", lambda _root, *args: None)
    (tmp_path / ".sync_commit").write_text("abc123\n", encoding="utf-8")

    manifest = pipeline._git_manifest(tmp_path)

    assert manifest["commit"] == "abc123"
    assert manifest["source"] == "sync_commit"
    assert manifest["dirty"] is False


def test_capture_reapplies_state_and_writes_positions(tmp_path: Path, monkeypatch) -> None:
    events: list[tuple[str, object | None]] = []
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

    monkeypatch.setenv("MCDATA_WORK_DIR", str(tmp_path / "instances"))
    monkeypatch.setenv("MCDATA_OUTPUT_DIR", str(tmp_path / "runs"))
    (tmp_path / "instances" / "matrix_low").mkdir(parents=True)
    monkeypatch.setattr(pipeline, "load_profile", lambda _configs, _name: dict(profile))
    monkeypatch.setattr(pipeline, "_start_replay_thread", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline, "start_server", lambda *args, **kwargs: FakeProc())
    monkeypatch.setattr(pipeline, "expected_scene_fill_count", lambda _profile: 2)
    monkeypatch.setattr(pipeline, "verify_scene_commands", lambda *args, **kwargs: 2)
    monkeypatch.setattr(pipeline, "wait_for_player_join", lambda *args, **kwargs: events.append(("join", None)))
    monkeypatch.setattr(pipeline, "apply_join_state", lambda _proc, _profile: events.append(("apply_join_state", None)))
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
    monkeypatch.setattr(pipeline, "write_positions_jsonl", lambda *args, **kwargs: events.append(("positions_written", None)) or 2)
    monkeypatch.setattr(pipeline, "_terminate_process_tree", lambda proc, *, timeout: events.append(("terminate", timeout)))
    monkeypatch.setattr(pipeline, "_resource_manifest", lambda _work_dir, _profile: {
        "asset_set": "vanilla",
        "mods": [],
        "resourcepacks": [],
        "shaderpacks": [],
    })
    monkeypatch.setattr(pipeline, "_env_manifest", lambda *, display: {"hostname": "host", "display": display})
    monkeypatch.setattr(pipeline, "_git_manifest", lambda _root: {"commit": "abc", "dirty": False})
    monkeypatch.setattr(pipeline.subprocess, "Popen", lambda *args, **kwargs: FakeProc())

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

    assert events[:4] == [
        ("join", None),
        ("apply_join_state", None),
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

    monkeypatch.setenv("MCDATA_WORK_DIR", str(tmp_path / "instances"))
    monkeypatch.setenv("MCDATA_OUTPUT_DIR", str(tmp_path / "runs"))
    (tmp_path / "instances" / "matrix_low").mkdir(parents=True)
    monkeypatch.setattr(pipeline, "load_profile", lambda _configs, _name: dict(profile))
    monkeypatch.setattr(pipeline, "_start_replay_thread", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline, "start_server", lambda *args, **kwargs: FakeProc())
    monkeypatch.setattr(pipeline, "expected_scene_fill_count", lambda _profile: 2)
    monkeypatch.setattr(pipeline, "verify_scene_commands", lambda *args, **kwargs: 2)
    monkeypatch.setattr(pipeline, "wait_for_player_join", lambda *args, **kwargs: events.append(("join", None)))
    monkeypatch.setattr(pipeline, "apply_join_state", lambda _proc, _profile: events.append(("apply_join_state", None)))
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
    monkeypatch.setattr(pipeline, "write_positions_jsonl", lambda *args, **kwargs: events.append(("positions_written", None)) or 2)
    monkeypatch.setattr(pipeline, "_terminate_process_tree", lambda proc, *, timeout: events.append(("terminate", timeout)))
    monkeypatch.setattr(pipeline, "_resource_manifest", lambda _work_dir, _profile: {
        "asset_set": "vanilla",
        "mods": [],
        "resourcepacks": [],
        "shaderpacks": [],
    })
    monkeypatch.setattr(pipeline, "_env_manifest", lambda *, display: {"hostname": "host", "display": display})
    monkeypatch.setattr(pipeline, "_git_manifest", lambda _root: {"commit": "abc", "dirty": False})
    monkeypatch.setattr(pipeline.subprocess, "Popen", lambda *args, **kwargs: FakeProc())

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


def test_bootstrap_manifest_records_lane_server_dir_and_port(tmp_path: Path, monkeypatch) -> None:
    profile = {
        "loader": "vanilla",
        "quality": "low",
        "asset_set": "vanilla",
        "width": 320,
        "height": 180,
        "username": "mcdata_bot",
        "jvm_args": "-Xmx1G",
        "server_dir": "servers",
        "server_port": 25570,
        "world_profile": "render_matrix_base",
    }

    monkeypatch.setenv("MCDATA_WORK_DIR", str(tmp_path / "instances"))
    monkeypatch.setenv("MCDATA_MAIN_DIR", str(tmp_path / "launcher"))
    monkeypatch.setattr(pipeline, "load_profile", lambda _configs, _name: dict(profile))
    monkeypatch.setattr(pipeline, "load_asset_config", lambda _configs: {})
    monkeypatch.setattr(pipeline, "resolve_game_version", lambda _profile: "26.2")
    monkeypatch.setattr(pipeline, "install_asset_set", lambda *_args, **_kwargs: ([], None))

    def fake_run(_cmd, **_kwargs):
        return subprocess.CompletedProcess(_cmd, 0)

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)

    result = pipeline.bootstrap_profile(
        tmp_path,
        "matrix_low",
        game_version="26.2",
        server_port=25604,
        lane="gpu4",
    )

    manifest = json.loads(
        (Path(result["work_dir"]) / "mcdata_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["server_port"] == 25604
    assert manifest["lane"] == "gpu4"
    assert manifest["server_dir"].endswith("servers/render_matrix_base__gpu4")


def test_concurrent_dry_run_processes_isolate_lane_port_and_display(tmp_path: Path) -> None:
    helper = tmp_path / "dry_run_helper.py"
    helper.write_text(
        """
import json
import os
import subprocess
import sys
from pathlib import Path

from mcdata import cli
from mcdata.render import pipeline

root = Path(sys.argv[1])
lane = sys.argv[2]
server_port = int(sys.argv[3])
display = sys.argv[4]

os.environ["MCDATA_WORK_DIR"] = str(root / "instances")
os.environ["MCDATA_OUTPUT_DIR"] = str(root / "runs")
(root / "instances" / "matrix_low").mkdir(parents=True, exist_ok=True)

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
}

pipeline.load_profile = lambda _configs, _name: dict(profile)
pipeline.resolve_game_version = lambda _profile: "26.2"
pipeline._resource_manifest = lambda _work_dir, _profile: {
    "asset_set": _profile.get("asset_set", "vanilla"),
    "mods": [],
    "resourcepacks": [],
    "shaderpacks": [],
}
pipeline._env_manifest = lambda *, display: {
    "hostname": "host",
    "display": display,
    "platform": "test",
    "python": "3",
    "gl_renderer": None,
    "gpu": [],
}
pipeline._git_manifest = lambda _root: {"commit": "abc", "dirty": False}
pipeline.subprocess.run = lambda cmd, **_kwargs: subprocess.CompletedProcess(cmd, 0)

cli.run(
    profile="matrix_low",
    root=root,
    dry_run=True,
    capture=False,
    strategy=None,
    duration=1,
    with_server=True,
    replay_actions=False,
    display=display,
    server_port=server_port,
    lane=lane,
    game_version="26.2",
)

run_dir = sorted((root / "runs").glob(f"*_matrix_low__{lane}"))[-1]
manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
print(json.dumps({
    "run_dir": str(run_dir),
    "lane": manifest["lane"],
    "server_port": manifest["profile"]["server_port"],
    "display": manifest["capture"]["settings"]["display"],
}))
""".strip()
        + "\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    src = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

    procs = [
        subprocess.Popen(
            [sys.executable, str(helper), str(tmp_path), "gpu0", "25600", ":77"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        ),
        subprocess.Popen(
            [sys.executable, str(helper), str(tmp_path), "gpu1", "25601", ":78"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        ),
    ]
    outputs = []
    for proc in procs:
        stdout, stderr = proc.communicate(timeout=30)
        assert proc.returncode == 0, stderr
        outputs.append(json.loads(stdout.splitlines()[-1]))

    by_lane = {item["lane"]: item for item in outputs}
    assert by_lane["gpu0"]["server_port"] == 25600
    assert by_lane["gpu0"]["display"] == ":77"
    assert by_lane["gpu1"]["server_port"] == 25601
    assert by_lane["gpu1"]["display"] == ":78"
    assert by_lane["gpu0"]["run_dir"] != by_lane["gpu1"]["run_dir"]
