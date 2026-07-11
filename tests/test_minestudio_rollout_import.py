from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from jsonschema import validate

from mcdata.action_source import validate_external_rollout_binding
from mcdata.action_trace import ActionTraceError
from mcdata.dataset import DatasetValidationError
from mcdata.dataset_support.episodes import _validate_manifest
from mcdata.external_action_adapters import MineStudioVPTEnvAdapter
from mcdata.minestudio_rollout_import import (
    MineStudioRolloutImportError,
    import_minestudio_rollout,
    rollout_manifest_sha256,
    validate_rollout_manifest,
)
from mcdata.render.pipeline import _trajectory_manifest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/minestudio_neutral_rollout"
CALIBRATION = ROOT / "tests/fixtures/minestudio_target_calibration.json"
RUNNER_PATH = ROOT / "external/minestudio_vpt_neutral/runner.py"


def _manifest() -> dict[str, Any]:
    return json.loads((FIXTURE / "rollout_manifest.json").read_text(encoding="utf-8"))


def _load_runner() -> Any:
    name = "mcdata_test_minestudio_neutral_runner"
    spec = importlib.util.spec_from_file_location(name, RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_rollout_manifest_matches_schema_and_semantic_hash() -> None:
    manifest = _manifest()
    schema = json.loads(
        (ROOT / "src/mcdata/schemas/minestudio_neutral_rollout.schema.json").read_text(
            encoding="utf-8"
        )
    )

    validate(instance=manifest, schema=schema)
    assert validate_rollout_manifest(manifest) == manifest
    assert manifest["rollout_sha256"] == rollout_manifest_sha256(manifest)
    assert manifest["producer"]["license_status"] == "license_unknown"
    assert manifest["producer"]["usage_scope"] == "research_only"
    runner = _runner()
    assert runner.MINESTUDIO_VERSION == "1.1.5"
    assert runner.MINESTUDIO_COMMIT == manifest["source_environment"]["repository_commit"]
    assert runner.MODEL_REVISION == manifest["producer"]["revision"]
    assert runner.MODEL_SHA256 == manifest["producer"]["model_sha256"]
    assert runner.ENGINE_REVISION == manifest["engine"]["revision"]
    assert runner.ENGINE_ARCHIVE_SHA256 == manifest["engine"]["archive_sha256"]


def test_import_matches_trace_and_trajectory_goldens(tmp_path: Path) -> None:
    trace_out = tmp_path / "native_trace.json"
    trajectory_out = tmp_path / "trajectory.json"

    result = import_minestudio_rollout(
        FIXTURE,
        expected_rollout_sha256=_manifest()["rollout_sha256"],
        expected_ticks=4,
        camera_calibration_path=CALIBRATION,
        trace_out=trace_out,
        trajectory_out=trajectory_out,
    )

    golden_trace = ROOT / "tests/golden/action_traces/minestudio_neutral_phase2_trace.json"
    golden_trajectory = ROOT / "tests/golden/action_traces/minestudio_neutral_phase2_trajectory.json"
    assert trace_out.read_bytes() == golden_trace.read_bytes()
    assert trajectory_out.read_bytes() == golden_trajectory.read_bytes()
    assert result == {
        "rollout_sha256": _manifest()["rollout_sha256"],
        "trace_sha256": json.loads(trace_out.read_text())["trace_sha256"],
        "tick_count": 4,
        "trajectory_event_count": 7,
        "source_minecraft_version": "1.16.5",
        "target_minecraft_version": "26.2",
        "compatibility_status": "target_replay_not_yet_validated",
    }
    trajectory = json.loads(trajectory_out.read_text())
    assert trajectory["curriculum_binding"]["status"] == "l1_candidate"
    assert trajectory["external_rollout_binding"]["source_minecraft_version"] == "1.16.5"
    assert trajectory["external_rollout_binding"]["target_minecraft_version"] == "26.2"
    assert validate_external_rollout_binding(trajectory["external_rollout_binding"]) == trajectory[
        "external_rollout_binding"
    ]
    trajectory_manifest = _trajectory_manifest(
        trajectory_out, source_path=trajectory_out, strategy="external_vpt_neutral"
    )
    assert trajectory_manifest is not None
    assert trajectory_manifest["external_rollout_binding"] == trajectory[
        "external_rollout_binding"
    ]


def test_unvalidated_target_replay_cannot_enter_dataset(tmp_path: Path) -> None:
    trajectory = json.loads(
        (ROOT / "tests/golden/action_traces/minestudio_neutral_phase2_trajectory.json").read_text()
    )
    manifest = {
        "profile": {"name": "fixture"},
        "git": {},
        "resources": {"resourcepack_runtime": {}},
        "world": {"seed": 1, "profile": "fixture-world", "state": {"time": "noon"}},
        "trajectory": {
            "sha256": "a" * 64,
            "external_rollout_binding": trajectory["external_rollout_binding"],
        },
        "mc_version": "26.2",
    }

    with pytest.raises(DatasetValidationError, match="not yet compatibility-validated"):
        _validate_manifest(manifest, tmp_path / "unvalidated-external-run")


def test_external_rollout_binding_validates_in_run_manifest_schema() -> None:
    manifest = json.loads(
        (ROOT / "docs/examples/run_manifest_example.json").read_text(encoding="utf-8")
    )
    trajectory_manifest = _trajectory_manifest(
        ROOT / "tests/golden/action_traces/minestudio_neutral_phase2_trajectory.json",
        source_path=ROOT
        / "tests/golden/action_traces/minestudio_neutral_phase2_trajectory.json",
        strategy=None,
    )
    assert trajectory_manifest is not None
    manifest["trajectory"] = trajectory_manifest
    schema = json.loads(
        (ROOT / "src/mcdata/schemas/manifest.schema.json").read_text(encoding="utf-8")
    )

    validate(instance=manifest, schema=schema)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value["producer"].update(usage_scope="publishable_train"), "producer"),
        (lambda value: value["rollout"].update(tick_rate_hz=30), "20 Hz"),
        (
            lambda value: value["rollout"].update(
                callback_order=["post_mapper_env_action_recorder", "neutral_mask"]
            ),
            "callback order",
        ),
        (lambda value: value["engine"].update(revision="mutable-main"), "engine"),
    ],
)
def test_manifest_contract_tampering_fails_closed(mutation: Any, message: str) -> None:
    manifest = _manifest()
    mutation(manifest)
    manifest["rollout_sha256"] = rollout_manifest_sha256(manifest)

    with pytest.raises(MineStudioRolloutImportError, match=message):
        validate_rollout_manifest(manifest)


def test_import_rejects_wrong_expected_hash_tick_count_and_action_bytes(tmp_path: Path) -> None:
    common = {
        "rollout_dir": FIXTURE,
        "expected_rollout_sha256": _manifest()["rollout_sha256"],
        "expected_ticks": 4,
        "camera_calibration_path": CALIBRATION,
        "trace_out": tmp_path / "trace.json",
        "trajectory_out": tmp_path / "trajectory.json",
    }
    with pytest.raises(MineStudioRolloutImportError, match="expected-rollout"):
        import_minestudio_rollout(**{**common, "expected_rollout_sha256": "f" * 64})
    with pytest.raises(MineStudioRolloutImportError, match="has 4 ticks"):
        import_minestudio_rollout(**{**common, "expected_ticks": 5})

    copied = tmp_path / "tampered"
    copied.mkdir()
    for source in FIXTURE.iterdir():
        (copied / source.name).write_bytes(source.read_bytes())
    actions = copied / "env_actions.jsonl"
    actions.write_bytes(actions.read_bytes().replace(b'"jump":0', b'"jump":1', 1))
    with pytest.raises(MineStudioRolloutImportError, match="size/SHA-256"):
        import_minestudio_rollout(**{**common, "rollout_dir": copied})


def test_import_rejects_non_mc26_calibration_and_path_traversal(tmp_path: Path) -> None:
    calibration = json.loads(CALIBRATION.read_text())
    calibration["target_minecraft_version"] = "1.16.5"
    bad_calibration = tmp_path / "bad-calibration.json"
    bad_calibration.write_text(json.dumps(calibration), encoding="utf-8")
    common = {
        "rollout_dir": FIXTURE,
        "expected_rollout_sha256": _manifest()["rollout_sha256"],
        "expected_ticks": 4,
        "camera_calibration_path": bad_calibration,
        "trace_out": tmp_path / "trace.json",
        "trajectory_out": tmp_path / "trajectory.json",
    }
    with pytest.raises(MineStudioRolloutImportError, match="target must be Minecraft 26.2"):
        import_minestudio_rollout(**common)

    manifest = _manifest()
    manifest["reset_contract"]["path"] = "../reset_contract.json"
    manifest["rollout_sha256"] = rollout_manifest_sha256(manifest)
    with pytest.raises(MineStudioRolloutImportError, match="canonical relative path"):
        validate_rollout_manifest(manifest)


def test_minestudio_adapter_accepts_only_inactive_unrepresented_drop() -> None:
    base = {"camera": [0.0, 0.0], "drop": 0}
    ticks = MineStudioVPTEnvAdapter().adapt([base])
    assert ticks[0]["held_buttons"] == []

    with pytest.raises(ActionTraceError, match="cannot be represented"):
        MineStudioVPTEnvAdapter().adapt([{**base, "drop": 1}])


class _FakeCallback:
    def before_step(self, sim: Any, action: dict[str, Any]) -> dict[str, Any]:
        del sim
        return action


class _FakeMaskCallback(_FakeCallback):
    def __init__(self, **settings: int) -> None:
        self.settings = settings

    def before_step(self, sim: Any, action: dict[str, Any]) -> dict[str, Any]:
        del sim
        action.update(self.settings)
        return action


class _UnusedRecordCallback(_FakeCallback):
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class _FakePolicy:
    loaded_from: str | None = None

    @classmethod
    def from_pretrained(cls, path: str) -> _FakePolicy:
        cls.loaded_from = path
        return cls()

    def to(self, device: str) -> _FakePolicy:
        self.device = device
        return self

    def eval(self) -> None:
        self.evaluating = True

    def get_action(
        self,
        observations: dict[str, Any],
        memory: Any,
        *,
        deterministic: bool,
        input_shape: str,
    ) -> tuple[dict[str, int], int]:
        del observations
        assert deterministic is True and input_shape == "*"
        tick = 0 if memory is None else memory + 1
        return {"buttons": tick, "camera": tick}, tick


class _FakeCuda:
    @staticmethod
    def is_available() -> bool:
        return False

    @staticmethod
    def manual_seed_all(seed: int) -> None:
        del seed


class _FakeTorch:
    __version__ = "fixture-torch"
    cuda = _FakeCuda()
    version = SimpleNamespace(cuda=None)
    backends = SimpleNamespace(cudnn=SimpleNamespace(benchmark=True, deterministic=False))

    @staticmethod
    def manual_seed(seed: int) -> None:
        _FakeTorch.seed = seed

    @staticmethod
    def use_deterministic_algorithms(enabled: bool) -> None:
        _FakeTorch.deterministic_algorithms = enabled


class _FakeSim:
    instances: list[_FakeSim] = []
    fail_tick: int | None = None

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.callbacks = kwargs["callbacks"]
        self.tick = 0
        self.closed = False
        self.instances.append(self)

    def reset(self) -> tuple[dict[str, Any], dict[str, Any]]:
        return {"image": np.zeros((128, 128, 3), dtype=np.uint8)}, {}

    def step(self, agent_action: dict[str, int]) -> tuple[Any, float, bool, bool, dict]:
        del agent_action
        if self.fail_tick == self.tick:
            raise RuntimeError("fake simulator step failure")
        action = {control: 0 for control in _runner().ENV_CONTROLS}
        action.update(
            {
                "forward": 1 if self.tick < 2 else 0,
                "jump": 1,
                "use": 1,
                "attack": 1,
                "inventory": 1,
                "drop": 1,
                "hotbar.1": 1,
                "camera": np.array([0.0, float(self.tick + 1)]),
            }
        )
        for callback in self.callbacks:
            action = callback.before_step(self, action)
        self.tick += 1
        observation = {"image": np.zeros((128, 128, 3), dtype=np.uint8)}
        return observation, 0.0, False, False, {}

    def close(self) -> None:
        self.closed = True


_RUNNER: Any | None = None


def _runner() -> Any:
    global _RUNNER
    if _RUNNER is None:
        _RUNNER = _load_runner()
    return _RUNNER


def _fake_runner_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any]:
    runner = _runner()
    repository = tmp_path / "MineStudio"
    module = repository / "minestudio/simulator/entry.py"
    module.parent.mkdir(parents=True)
    module.write_text("# fake\n", encoding="utf-8")
    home = tmp_path / "runtime"
    jar = home / runner.ENGINE_RUNTIME_JAR
    jar.parent.mkdir(parents=True)
    archive = home / runner.ENGINE_ARCHIVE_FILENAME
    archive.write_bytes(b"fixture-engine-archive")
    jar.write_bytes(b"fixture-engine-jar")
    model = tmp_path / "model"
    model.mkdir()
    (model / runner.MODEL_FILENAME).write_bytes(b"fixture-model")
    (model / "config.json").write_bytes(b"{}\n")
    reset = tmp_path / "reset.json"
    reset.write_bytes(
        (ROOT / "external/minestudio_vpt_neutral/reset_contract.example.json").read_bytes()
    )
    monkeypatch.setattr(runner, "_verify_minestudio_checkout", lambda path: None)
    monkeypatch.setattr(runner, "_java8_version", lambda: 'openjdk version "1.8.0_fixture"')
    monkeypatch.setattr(runner.importlib.metadata, "version", lambda package: runner.MINESTUDIO_VERSION)
    monkeypatch.setattr(runner, "ENGINE_ARCHIVE_SHA256", _sha(archive))
    monkeypatch.setattr(runner, "MODEL_SHA256", _sha(model / runner.MODEL_FILENAME))
    monkeypatch.setattr(runner, "MODEL_CONFIG_SHA256", _sha(model / "config.json"))
    runtime = runner.RuntimeBindings(
        minecraft_sim=_FakeSim,
        callback_base=_FakeCallback,
        mask_callback=_FakeMaskCallback,
        record_callback=_UnusedRecordCallback,
        vpt_policy=_FakePolicy,
        numpy=np,
        torch=_FakeTorch,
        minestudio_module_file=str(module),
    )
    config = runner.RunnerConfig(
        minestudio_repo=repository,
        minestudio_home=home,
        model_dir=model,
        reset_contract=reset,
        output_dir=tmp_path / "rollout",
        ticks=3,
        device="cpu",
        save_frames=False,
        save_video=False,
    )
    return config, runtime


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_fake_runner_records_post_mapper_actions_after_neutral_mask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, runtime = _fake_runner_inputs(tmp_path, monkeypatch)
    _FakeSim.instances.clear()
    _FakeSim.fail_tick = None

    manifest = _runner().run_rollout(config, runtime=runtime)

    sim = _FakeSim.instances[-1]
    assert sim.kwargs["action_type"] == "agent"
    assert sim.closed is True
    assert manifest["rollout"]["tick_count"] == 3
    assert manifest["rollout"]["callback_order"] == [
        "neutral_mask",
        "post_mapper_env_action_recorder",
    ]
    records = [
        json.loads(line)
        for line in (config.output_dir / "env_actions.jsonl").read_text().splitlines()
    ]
    assert [item["camera"] for item in records] == [[0.0, 1.0], [0.0, 2.0], [0.0, 3.0]]
    assert [item["forward"] for item in records] == [1, 1, 0]
    assert all(item[control] == 0 for item in records for control in _runner().MASKED_CONTROLS)
    assert manifest["rollout_sha256"] == _runner()._semantic_sha256(
        manifest, exclude="rollout_sha256"
    )


def test_fake_runner_closes_and_removes_staging_on_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, runtime = _fake_runner_inputs(tmp_path, monkeypatch)
    _FakeSim.instances.clear()
    _FakeSim.fail_tick = 1

    with pytest.raises(RuntimeError, match="fake simulator step failure"):
        _runner().run_rollout(config, runtime=runtime)

    assert _FakeSim.instances[-1].closed is True
    assert not config.output_dir.exists()
    assert not config.output_dir.with_name(f".{config.output_dir.name}.tmp").exists()
    _FakeSim.fail_tick = None
