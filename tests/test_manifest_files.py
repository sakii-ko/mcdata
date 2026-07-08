import json
from pathlib import Path
import subprocess

from mcdata.render import pipeline
from mcdata.render.pipeline import _copy_trajectory


def test_copy_trajectory_uses_final_path_without_tmp_leftover(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"events": [{"t": 0.0}]}) + "\n", encoding="utf-8")
    run_dir = tmp_path / "run"

    copied = _copy_trajectory(run_dir, source)

    assert copied == run_dir / "trajectory.json"
    assert copied.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    assert not (run_dir / "trajectory.json.tmp").exists()


def test_window_geometry_prefers_largest_xdotool_match(monkeypatch) -> None:
    monkeypatch.setattr(pipeline.shutil, "which", lambda name: "/usr/bin/xdotool" if name == "xdotool" else None)

    def fake_run(cmd, **_kwargs):
        if cmd[:3] == ["xdotool", "search", "--name"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="101\n202\n", stderr="")
        if cmd == ["xdotool", "getwindowgeometry", "--shell", "101"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="X=9\nY=8\nWIDTH=320\nHEIGHT=180\n", stderr="")
        if cmd == ["xdotool", "getwindowgeometry", "--shell", "202"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="X=12\nY=34\nWIDTH=1280\nHEIGHT=720\n", stderr="")
        raise AssertionError(cmd)

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)

    assert pipeline._minecraft_window_geometry("Minecraft") == (12, 34, 1280, 720)
    assert pipeline._window_geometry_record(requested_width=1280, requested_height=720) == {
        "geometry": {"x": 12, "y": 34, "width": 1280, "height": 720},
        "warning": None,
    }


def test_window_geometry_moves_offscreen_xdotool_match(monkeypatch) -> None:
    monkeypatch.setattr(pipeline.shutil, "which", lambda name: "/usr/bin/xdotool" if name == "xdotool" else None)
    moved = False

    def fake_run(cmd, **_kwargs):
        nonlocal moved
        if cmd[:3] == ["xdotool", "search", "--name"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="202\n", stderr="")
        if cmd == ["xdotool", "getwindowgeometry", "--shell", "202"]:
            stdout = (
                "X=0\nY=0\nWIDTH=1280\nHEIGHT=720\n"
                if moved
                else "X=-128\nY=-72\nWIDTH=1280\nHEIGHT=720\n"
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
        if cmd == ["xdotool", "windowmove", "202", "0", "0"]:
            moved = True
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(cmd)

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)

    assert pipeline._minecraft_window_geometry("Minecraft") == (0, 0, 1280, 720)
    assert moved is True
    assert pipeline._window_geometry_record(requested_width=1280, requested_height=720) == {
        "geometry": {"x": 0, "y": 0, "width": 1280, "height": 720},
        "warning": None,
    }


def test_window_geometry_falls_back_to_xwininfo(monkeypatch) -> None:
    monkeypatch.setattr(pipeline.shutil, "which", lambda _name: None)

    def fake_run(cmd, **_kwargs):
        assert cmd == ["xwininfo", "-name", "Minecraft"]
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=(
                "Absolute upper-left X:  3\n"
                "Absolute upper-left Y:  4\n"
                "Width: 640\n"
                "Height: 360\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)

    assert pipeline._minecraft_window_geometry("Minecraft") == (3, 4, 640, 360)
    assert pipeline._window_geometry_record(requested_width=1280, requested_height=720) == {
        "geometry": {"x": 3, "y": 4, "width": 640, "height": 360},
        "warning": "size_mismatch",
    }


def test_capture_input_falls_back_for_offscreen_window(monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "_minecraft_window_geometry", lambda window_name="Minecraft": (-128, -72, 1280, 720))

    assert pipeline._capture_input(":77", width=1280, height=720, desktop=False) == ":77"


def test_git_manifest_falls_back_to_sync_commit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "_git_output", lambda _root, *args: None)
    (tmp_path / ".sync_commit").write_text("abc123\n", encoding="utf-8")

    manifest = pipeline._git_manifest(tmp_path)

    assert manifest["commit"] == "abc123"
    assert manifest["source"] == "sync_commit"
    assert manifest["dirty"] is False


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
