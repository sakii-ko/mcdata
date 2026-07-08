from pathlib import Path

from mcdata import cli
from mcdata.settings import CaptureSettings


def test_bootstrap_passes_game_version(tmp_path: Path, monkeypatch) -> None:
    calls: list[dict] = []

    def fake_bootstrap_profile(_root: Path, _profile: str, **kwargs) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(cli, "bootstrap_profile", fake_bootstrap_profile)

    cli.bootstrap(profile="matrix_low", root=tmp_path, game_version="26.2")

    assert calls == [{"game_version": "26.2"}]


def test_run_passes_hidden_debug_flags(tmp_path: Path, monkeypatch) -> None:
    calls: list[dict] = []

    def fake_generate_strategy(_configs: Path, strategy: str, out: Path) -> dict:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text('{"events": []}\n', encoding="utf-8")
        return {"events": []}

    def fake_launch_profile(_root: Path, _profile: str, **kwargs) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(cli, "generate_strategy", fake_generate_strategy)
    monkeypatch.setattr(cli, "launch_profile", fake_launch_profile)

    cli.run(
        profile="matrix_low",
        root=tmp_path,
        dry_run=False,
        capture=True,
        strategy="ground_astar_loop",
        duration=5,
        with_server=True,
        replay_actions=True,
        display=None,
        server_port=None,
        lane=None,
        game_version="26.2",
        probe_interval=1.25,
        debug_no_reapply=True,
        debug_no_replay_gate=True,
    )

    assert calls[0]["probe_interval"] == 1.25
    assert calls[0]["debug_no_reapply"] is True
    assert calls[0]["debug_no_replay_gate"] is True


def test_run_matrix_uses_lane_trajectory_and_overrides(tmp_path: Path, monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []

    def fake_generate_strategy(_configs: Path, strategy: str, out: Path) -> dict:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text('{"events": []}\n', encoding="utf-8")
        calls.append(("generate", {"strategy": strategy, "out": out}))
        return {"events": []}

    def fake_bootstrap_profile(_root: Path, profile: str, **kwargs) -> None:
        calls.append(("bootstrap", {"profile": profile, **kwargs}))

    def fake_launch_profile(_root: Path, profile: str, **kwargs) -> None:
        calls.append(("launch", {"profile": profile, **kwargs}))

    monkeypatch.setattr(cli, "generate_strategy", fake_generate_strategy)
    monkeypatch.setattr(cli, "load_profile", lambda _configs, _name: {"game_version": "26.2"})
    def fail_resolve(_profile):
        raise AssertionError("explicit --game-version should skip resolver")

    monkeypatch.setattr(cli, "resolve_game_version", fail_resolve)
    monkeypatch.setattr(cli, "bootstrap_profile", fake_bootstrap_profile)
    monkeypatch.setattr(cli, "launch_profile", fake_launch_profile)

    cli.run_matrix(
        profiles="matrix_low,matrix_textured",
        root=tmp_path,
        strategy="ground_astar_loop",
        duration=5,
        capture=False,
        with_server=False,
        replay_actions=False,
        bootstrap=True,
        display=":78",
        server_port=25601,
        lane="gpu1",
        probe_interval=1.5,
        game_version="26.2",
    )

    generated = [call for call in calls if call[0] == "generate"]
    assert generated[0][1]["out"] == tmp_path / "runs" / "trajectories" / "ground_astar_loop_matrix_gpu1.json"

    bootstraps = [call[1] for call in calls if call[0] == "bootstrap"]
    launches = [call[1] for call in calls if call[0] == "launch"]
    assert [item["profile"] for item in bootstraps] == ["matrix_low", "matrix_textured"]
    assert [item["profile"] for item in launches] == ["matrix_low", "matrix_textured"]
    assert {item["server_port"] for item in bootstraps + launches} == {25601}
    assert {item["lane"] for item in bootstraps + launches} == {"gpu1"}
    assert {item["game_version"] for item in bootstraps + launches} == {"26.2"}
    assert {item["probe_interval"] for item in launches} == {1.5}
    assert CaptureSettings.from_env({"width": 320, "height": 180}).display == ":78"
