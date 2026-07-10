import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import validate

from mcdata.action_effect import (
    ActionEffectError,
    REPORT_FILENAME,
    action_effect_manifest_evidence,
    build_action_effect_report,
    validate_action_effect_report,
    write_action_effect_report,
)
from mcdata.render import pipeline

ROOT = Path(__file__).resolve().parents[1]
REJECTED_L2_FIXTURE = ROOT / "tests/fixtures/action_effect_first_l2_rejected.json"


def _canonical_run(tmp_path: Path, *, jump: bool = True, step: float = 0.1) -> Path:
    run = tmp_path / "run"
    run.mkdir()
    events = []
    for index, press_t in enumerate((1.0, 3.0, 5.0, 7.0), 1):
        common = {
            "key": "space",
            "semantic_action": "deliberate_jump",
            "jump_id": f"jump_{index}",
            "route_index": index * 10,
            "hold_duration_sec": 0.16,
        }
        events.extend(
            [
                {
                    "t": press_t,
                    "action": "down",
                    "semantic_phase": "press",
                    **common,
                },
                {
                    "t": press_t + 0.16,
                    "action": "up",
                    "semantic_phase": "release",
                    **common,
                },
            ]
        )
    recovery = {"t": 8.8, "key": "space", "action": "tap"}
    trajectory = {
        "action_curriculum": {
            "taxonomy_version": 1,
            "planned_level": 2,
            "capabilities": ["navigation", "deliberate_jump"],
        },
        "events": [*events, recovery],
    }
    _write_json(run / "trajectory.json", trajectory)
    replay = [{"event": "start", "mono": 10.0}]
    for event in trajectory["events"]:
        replay.append(
            {
                "event": event,
                "scheduled_t": event["t"],
                "actual_t": event["t"] + 0.01,
                "execution_status": "executed",
            }
        )
    _write_jsonl(run / "replay_log.jsonl", replay)
    rows = []
    count = round(9.5 / step)
    for index in range(count + 1):
        t_rel = round(index * step, 6)
        delta = 0.0
        if jump:
            for press_t in (1.01, 3.01, 5.01, 7.01):
                age = t_rel - press_t
                if 0.0 <= age <= 0.8:
                    delta = max(delta, 1.2 * max(0.0, 1.0 - abs(age - 0.3) / 0.3))
        rows.append({"idx": index, "t_rel": t_rel, "x": t_rel, "y": 64.0 + delta, "z": 0.0})
    _write_jsonl(run / "positions.jsonl", rows)
    return run


def test_physical_jump_report_passes_and_is_stable_schema_valid(tmp_path: Path) -> None:
    run = _canonical_run(tmp_path)

    first = write_action_effect_report(run)
    first_bytes = (run / REPORT_FILENAME).read_bytes()
    second = write_action_effect_report(run)

    schema = json.loads(
        (ROOT / "src/mcdata/schemas/action_effect_report.schema.json").read_text()
    )
    validate(first, schema)
    assert first == second == validate_action_effect_report(run)
    assert first_bytes == (run / REPORT_FILENAME).read_bytes()
    assert first["accepted"] is True
    assert first["planned_jump_count"] == first["verified_jump_count"] == 4
    assert all(item["peak"]["delta_y"] >= 0.8 for item in first["jumps"])
    assert all(item["landing"]["passed"] for item in first["jumps"])
    assert first["report_id"].startswith("sha256:")
    assert len(first["report_id"]) == 71

    evidence = action_effect_manifest_evidence(run / REPORT_FILENAME, first)
    assert evidence["accepted"] is True
    assert evidence["sha256"] == hashlib.sha256(first_bytes).hexdigest()


def test_dispatch_without_physical_jump_fails_closed(tmp_path: Path) -> None:
    report = build_action_effect_report(_canonical_run(tmp_path, jump=False))

    assert report["checks"]["replay_dispatch_complete"] is True
    assert report["checks"]["all_jumps_physical"] is False
    assert report["verified_jump_count"] == 0
    assert report["accepted"] is False
    assert all(item["peak"]["delta_y"] == 0.0 for item in report["jumps"])


def test_sparse_or_tampered_position_trace_fails_closed(tmp_path: Path) -> None:
    run = _canonical_run(tmp_path, step=0.25)
    report = write_action_effect_report(run)

    assert report["position_trace"]["max_gap_sec"] == 0.25
    assert report["checks"]["position_cadence"] is False
    assert report["accepted"] is False

    saved = json.loads((run / REPORT_FILENAME).read_text())
    saved["accepted"] = True
    _write_json(run / REPORT_FILENAME, saved)
    with pytest.raises(ActionEffectError, match="does not match source artifacts"):
        validate_action_effect_report(run)


def test_recovery_space_without_semantic_label_is_not_a_jump(tmp_path: Path) -> None:
    report = build_action_effect_report(_canonical_run(tmp_path))

    assert report["planned_jump_count"] == 4
    assert [item["jump_id"] for item in report["jumps"]] == [
        "jump_1",
        "jump_2",
        "jump_3",
        "jump_4",
    ]


def test_first_remote_l2_rejection_is_a_regression_fail_fixture(tmp_path: Path) -> None:
    report = build_action_effect_report(_historical_rejected_run(tmp_path))

    assert report["accepted"] is False
    assert report["planned_jump_count"] == 4
    assert report["checks"]["replay_semantic_alignment"] is True
    assert report["checks"]["semantic_jump_inputs_dispatched"] is True
    assert report["checks"]["canonical_jump_sequences"] is False
    assert sum(item["physical_effect_passed"] for item in report["jumps"]) == 1
    assert [item["peak"]["delta_y"] for item in report["jumps"]] == [
        0.0,
        1.249187,
        0.0,
        0.0,
    ]


def test_capture_pipeline_returns_a_fail_closed_physical_gate_error(tmp_path: Path) -> None:
    run = _historical_rejected_run(tmp_path)
    records = []

    class RunLog:
        def log(self, stage: str, event: str, **fields: object) -> None:
            records.append((stage, event, fields))

    evidence, error = pipeline._action_effect_for_plan(
        SimpleNamespace(
            run_trajectory_path=run / "trajectory.json",
            run_dir=run,
            replay_actions=True,
            dry_run=False,
        ),
        RunLog(),
    )

    assert evidence is not None and evidence["accepted"] is False
    assert error is not None and "physical deliberate-jump effect gate failed" in error
    assert records[0][0:2] == ("action_effect", "report_written")


def _historical_rejected_run(tmp_path: Path) -> Path:
    fixture = json.loads(REJECTED_L2_FIXTURE.read_text(encoding="utf-8"))
    run = tmp_path / "first_l2_rejected"
    run.mkdir()
    events = [
        {
            "t": item["scheduled_t_sec"],
            "key": "space",
            "action": "tap",
            "semantic_action": "deliberate_jump",
            "route_index": item["route_index"],
        }
        for item in fixture["jumps"]
    ]
    _write_json(
        run / "trajectory.json",
        {
            "action_curriculum": {
                "taxonomy_version": 1,
                "planned_level": 2,
                "capabilities": ["navigation", "deliberate_jump"],
            },
            "events": events,
        },
    )
    replay = [{"event": "start", "mono": 1.0}]
    replay.extend(
        {
            "event": event,
            "scheduled_t": event["t"],
            "actual_t": item["actual_dispatch_t_sec"],
            "execution_status": "executed",
        }
        for event, item in zip(events, fixture["jumps"])
    )
    _write_jsonl(run / "replay_log.jsonl", replay)
    step = fixture["position_sample_step_sec"]
    baseline = fixture["baseline_y"]
    rows = []
    for index in range(round(57.0 / step) + 1):
        t_rel = round(index * step, 6)
        y = baseline
        rows.append({"idx": index, "t_rel": t_rel, "x": 0.0, "y": y, "z": 0.0})
    for item in fixture["jumps"]:
        if item["peak_delta_y"] <= 0:
            continue
        rows.append(
            {
                "idx": -1,
                "t_rel": item["peak_t_sec"],
                "x": 0.0,
                "y": baseline + item["peak_delta_y"],
                "z": 0.0,
            }
        )
    rows.sort(key=lambda row: row["t_rel"])
    for index, row in enumerate(rows):
        row["idx"] = index
    _write_jsonl(run / "positions.jsonl", rows)
    return run


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, values: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(value, sort_keys=True) + "\n" for value in values),
        encoding="utf-8",
    )
