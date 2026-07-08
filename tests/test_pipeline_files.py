import json
import os
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

from mcdata.render import pipeline
from mcdata.render.pipeline import _copy_trajectory, _profile_with_overrides, _run_dir
from mcdata.render.server import ensure_server, server_profile_name


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
