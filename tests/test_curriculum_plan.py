from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import validate
from typer.testing import CliRunner

from mcdata import cli
from mcdata.dataset_support import curriculum_io
from mcdata.dataset_support.core import value_sha256
from mcdata.dataset_support.curriculum import (
    BUCKETS,
    CurriculumPlanError,
    build_curriculum_plan,
    parse_ratio_assignments,
    validate_curriculum_plan,
)
from mcdata.dataset_support.curriculum_io import write_curriculum_plan


def _source_index() -> dict:
    episodes = []
    pairs = []
    action_buckets = {"taxonomy_version": 1}
    for number, bucket in enumerate(BUCKETS, 1):
        source = f"episode-{bucket}-source"
        target = f"episode-{bucket}-target"
        episode_ids = [source, target]
        action_buckets[bucket] = {
            "episode_count": len(episode_ids),
            "episode_ids": sorted(episode_ids),
        }
        episodes.extend(
            {
                "episode_id": episode_id,
                "profile_name": "misleading-profile-name",
                "accepted": True,
                "action_curriculum": {"taxonomy_version": 1, "bucket": bucket},
            }
            for episode_id in episode_ids
        )
        pairs.append(
            {
                "pair_id": f"pair-{number:016x}",
                "source_episode": source,
                "target_episode": target,
            }
        )
    index = {
        "schema_version": 2,
        "status": "accepted",
        "episodes": episodes,
        "action_buckets": action_buckets,
        "pairs": pairs,
    }
    return _rehash(index)


def _rehash(index: dict) -> dict:
    result = copy.deepcopy(index)
    result.pop("dataset_id", None)
    result["dataset_id"] = f"sha256:{value_sha256(result)}"
    return result


def _ratios(*values: float) -> dict[str, float]:
    return dict(zip(BUCKETS, values, strict=True))


def _build(index: dict | None = None, **overrides) -> dict:
    arguments = {
        "source_index_sha256": "f" * 64,
        "stage_name": "stage-02-jump",
        "ratios": _ratios(0.4, 0.3, 0.2, 0.1),
        "epoch": 3,
        "sample_count": 11,
        "seed": 712,
    }
    arguments.update(overrides)
    return build_curriculum_plan(index or _source_index(), **arguments)


def test_build_curriculum_plan_is_deterministic_hash_bound_and_schema_valid() -> None:
    plan = _build()
    repeated = _build()

    assert plan == repeated
    assert plan["bucket_counts"] == {
        "l1": 5,
        "l1_l2": 3,
        "l1_l2_l3": 2,
        "l1_l2_l3_l4": 1,
    }
    assert len(plan["schedule"]) == 11
    assert [item["sample_index"] for item in plan["schedule"]] == list(range(11))
    assert plan["sampling_unit"] == "edit_pair"
    assert plan["source_index"]["dataset_id"] == _source_index()["dataset_id"]
    unsigned = dict(plan)
    unsigned.pop("plan_id")
    assert plan["plan_id"] == f"sha256:{value_sha256(unsigned)}"
    validate_curriculum_plan(plan)
    schema = json.loads(
        (
            Path(__file__).parents[1]
            / "src/mcdata/schemas/curriculum_plan.schema.json"
        ).read_text(encoding="utf-8")
    )
    validate(plan, schema)


def test_largest_remainder_ties_use_fixed_bucket_order() -> None:
    plan = _build(ratios=_ratios(0.25, 0.25, 0.25, 0.25), sample_count=2)

    assert plan["bucket_counts"] == {
        "l1": 1,
        "l1_l2": 1,
        "l1_l2_l3": 0,
        "l1_l2_l3_l4": 0,
    }


def test_pair_cycles_exhaust_each_bucket_before_replacement() -> None:
    index = _source_index()
    first = index["episodes"][0]["episode_id"]
    second = index["episodes"][1]["episode_id"]
    index["pairs"].append(
        {
            "pair_id": "pair-00000000000000a1",
            "source_episode": first,
            "target_episode": second,
        }
    )
    index = _rehash(index)

    plan = _build(
        index,
        ratios=_ratios(1.0, 0.0, 0.0, 0.0),
        sample_count=6,
    )
    selected = [item["pair_id"] for item in plan["schedule"]]

    assert all(len(set(selected[offset : offset + 2])) == 2 for offset in range(0, 6, 2))
    assert set(selected) == {"pair-0000000000000001", "pair-00000000000000a1"}


@pytest.mark.parametrize(
    ("ratios", "match"),
    [
        (_ratios(float("nan"), 0.0, 0.0, 1.0), "finite"),
        (_ratios(float("inf"), 0.0, 0.0, 0.0), "finite"),
        (_ratios(-0.1, 0.5, 0.3, 0.3), "non-negative"),
        (_ratios(0.1, 0.2, 0.3, 0.3), "sum exactly to 1"),
        ({"l1": 1.0}, "specify exactly"),
    ],
)
def test_invalid_ratios_fail_closed(ratios: dict, match: str) -> None:
    with pytest.raises(CurriculumPlanError, match=match):
        _build(ratios=ratios)


def test_positive_ratio_referencing_empty_bucket_fails() -> None:
    index = _source_index()
    keep = {"episode-l1-source", "episode-l1-target"}
    index["episodes"] = [item for item in index["episodes"] if item["episode_id"] in keep]
    index["pairs"] = [index["pairs"][0]]
    for bucket in BUCKETS[1:]:
        index["action_buckets"][bucket] = {"episode_count": 0, "episode_ids": []}
    index = _rehash(index)

    with pytest.raises(CurriculumPlanError, match="empty eligible pair bucket"):
        _build(index, ratios=_ratios(0.5, 0.5, 0.0, 0.0))


def test_pair_must_not_cross_action_buckets() -> None:
    index = _source_index()
    index["pairs"][0]["target_episode"] = "episode-l1_l2-target"
    index = _rehash(index)

    with pytest.raises(CurriculumPlanError, match="crosses action buckets"):
        _build(index)


def test_explicit_pair_allowlist_is_bound_without_inventing_a_split() -> None:
    allowed = ["pair-0000000000000001", "pair-0000000000000002"]
    plan = _build(
        ratios=_ratios(0.5, 0.5, 0.0, 0.0),
        allowed_pair_ids=list(reversed(allowed)),
    )

    assert plan["policy"]["pair_filter"] == {
        "mode": "explicit_pair_ids",
        "pair_ids": allowed,
    }
    assert plan["eligible_pair_ids"]["l1_l2_l3"] == []
    assert {item["pair_id"] for item in plan["schedule"]} == set(allowed)

    with pytest.raises(CurriculumPlanError, match="unknown IDs"):
        _build(allowed_pair_ids=["pair-ffffffffffffffff"])
    with pytest.raises(CurriculumPlanError, match="empty eligible pair bucket"):
        _build(
            ratios=_ratios(0.5, 0.5, 0.0, 0.0),
            allowed_pair_ids=["pair-0000000000000001"],
        )


def test_only_accepted_untampered_index_can_plan() -> None:
    automated = _source_index()
    automated["status"] = "automated_pass"
    automated = _rehash(automated)
    with pytest.raises(CurriculumPlanError, match="status='accepted'"):
        _build(automated)

    tampered = _source_index()
    tampered["episodes"][0]["profile_name"] = "tampered-after-indexing"
    with pytest.raises(CurriculumPlanError, match="dataset_id does not match"):
        _build(tampered)


def test_action_bucket_declaration_must_match_episode_claims() -> None:
    index = _source_index()
    index["action_buckets"]["l1"]["episode_count"] = 1
    index = _rehash(index)

    with pytest.raises(CurriculumPlanError, match="disagrees with episode claims"):
        _build(index)


def test_validate_plan_rejects_tampered_stable_schedule_even_with_new_id() -> None:
    plan = _build()
    plan["schedule"][0]["pair_id"] = "pair-ffffffffffffffff"
    unsigned = dict(plan)
    unsigned.pop("plan_id")
    plan["plan_id"] = f"sha256:{value_sha256(unsigned)}"

    with pytest.raises(CurriculumPlanError, match="stable expansion"):
        validate_curriculum_plan(plan)


def test_validate_plan_reports_malformed_pair_lists_without_type_errors() -> None:
    plan = _build()
    plan["eligible_pair_ids"]["l1"] = [["not-a-pair-id"]]

    with pytest.raises(CurriculumPlanError, match="Eligible pair IDs"):
        validate_curriculum_plan(plan)


def test_write_curriculum_plan_binds_exact_source_bytes_and_is_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "dataset_index.json"
    source.write_text(json.dumps(_source_index(), indent=1) + "\n", encoding="utf-8")
    out = tmp_path / "plans" / "stage.json"
    validated = []
    monkeypatch.setattr(
        curriculum_io, "validate_index_schema", lambda value: validated.append(value)
    )

    plan = write_curriculum_plan(
        source,
        out,
        stage_name="stage-l1",
        ratios=_ratios(1.0, 0.0, 0.0, 0.0),
        epoch=0,
        sample_count=4,
        seed=9,
    )

    assert validated == [_source_index()]
    assert plan["source_index"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert json.loads(out.read_text(encoding="utf-8")) == plan
    assert not (out.parent / "stage.json.tmp").exists()


def test_parse_ratio_assignments_requires_each_bucket_once() -> None:
    assert parse_ratio_assignments(
        ["l1=0.7", "l1_l2=0.2", "l1_l2_l3=0.1", "l1_l2_l3_l4=0"]
    ) == _ratios(0.7, 0.2, 0.1, 0.0)
    with pytest.raises(CurriculumPlanError, match="Duplicate ratio"):
        parse_ratio_assignments(
            ["l1=0.5", "l1=0.5", "l1_l2=0", "l1_l2_l3=0", "l1_l2_l3_l4=0"]
        )


def test_curriculum_plan_cli_parses_explicit_ratios_and_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    def fake_write(dataset_index: Path, out: Path, **kwargs) -> dict:
        calls.append((dataset_index, out, kwargs))
        return {"sample_count": kwargs["sample_count"], "plan_id": "sha256:" + "a" * 64}

    monkeypatch.setattr(cli, "write_curriculum_plan", fake_write)
    result = CliRunner().invoke(
        cli.app,
        [
            "curriculum-plan",
            str(tmp_path / "dataset_index.json"),
            "--out",
            str(tmp_path / "plan.json"),
            "--stage",
            "stage-l1-l2",
            "--ratio",
            "l1=0.75",
            "--ratio",
            "l1_l2=0.25",
            "--ratio",
            "l1_l2_l3=0",
            "--ratio",
            "l1_l2_l3_l4=0",
            "--epoch",
            "2",
            "--samples",
            "64",
            "--seed",
            "17",
            "--allow-pair-id",
            "pair-0000000000000001",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls[0][2] == {
        "stage_name": "stage-l1-l2",
        "ratios": _ratios(0.75, 0.25, 0.0, 0.0),
        "epoch": 2,
        "sample_count": 64,
        "seed": 17,
        "allowed_pair_ids": ["pair-0000000000000001"],
    }
    assert "64" in result.output and "edit pairs" in result.output


def test_curriculum_plan_cli_rejects_incomplete_ratios(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "write_curriculum_plan", pytest.fail)
    result = CliRunner().invoke(
        cli.app,
        [
            "curriculum-plan",
            "dataset_index.json",
            "--out",
            "plan.json",
            "--stage",
            "stage-l1",
            "--ratio",
            "l1=1",
            "--epoch",
            "0",
            "--samples",
            "8",
            "--seed",
            "1",
        ],
    )

    assert result.exit_code == 2
    assert "Ratios must specify exactly" in result.output
