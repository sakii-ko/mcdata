from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcdata.render.resourcepack_gate import (
    ResourcePackRuntimeError,
    inspect_resourcepack_runtime,
    validate_resourcepack_runtime,
)


def _reload(*packs: str) -> str:
    resources = ["vanilla", "fabricloader", *packs]
    return "[Render thread/INFO]: Reloading ResourceManager: " + ", ".join(resources)


def _write_log(work_dir: Path, *lines: str) -> Path:
    path = work_dir / "logs/latest.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_runtime_gate_passes_with_exact_pack_set_from_sidecar(tmp_path: Path) -> None:
    sidecar = tmp_path / ".mcdata/resourcepack-resolution.json"
    sidecar.parent.mkdir()
    sidecar.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "packs": [
                    {"filename": "alpha.zip"},
                    {"effective_path": str(tmp_path / "resourcepacks/beta pack.zip")},
                ],
            }
        ),
        encoding="utf-8",
    )
    _write_log(tmp_path, _reload("file/alpha.zip", "file/beta pack.zip"))

    record = validate_resourcepack_runtime(tmp_path, timeout_sec=0)

    assert record.status == "pass"
    assert record.expected_file_packs == ("file/alpha.zip", "file/beta pack.zip")
    assert record.actual_file_packs == record.expected_file_packs
    assert record.to_dict()["expected_source"] == str(sidecar)


@pytest.mark.parametrize(
    ("actual", "missing", "unexpected"),
    [
        (("file/alpha.zip",), ("file/beta.zip",), ()),
        (
            ("file/alpha.zip", "file/beta.zip", "file/unexpected.zip"),
            (),
            ("file/unexpected.zip",),
        ),
    ],
)
def test_runtime_gate_fails_on_missing_or_extra_pack(
    tmp_path: Path,
    actual: tuple[str, ...],
    missing: tuple[str, ...],
    unexpected: tuple[str, ...],
) -> None:
    _write_log(tmp_path, _reload(*actual))

    with pytest.raises(ResourcePackRuntimeError) as caught:
        validate_resourcepack_runtime(
            tmp_path,
            expected_filenames=["alpha.zip", "beta.zip"],
            timeout_sec=0,
        )

    assert caught.value.record.reason == "timeout:resource_pack_set_mismatch"
    assert caught.value.record.missing_file_packs == missing
    assert caught.value.record.unexpected_file_packs == unexpected


def test_runtime_gate_fails_immediately_on_bad_pack_metadata(tmp_path: Path) -> None:
    _write_log(
        tmp_path,
        "[Render thread/WARN]: Error reading pack metadata, attempting fallback type",
        "Pack declares support for version newer than 64, but is missing mandatory fields",
        _reload("file/alpha.zip"),
    )

    with pytest.raises(ResourcePackRuntimeError) as caught:
        validate_resourcepack_runtime(
            tmp_path,
            expected_filenames=["alpha.zip"],
            timeout_sec=10,
            sleep=lambda _seconds: pytest.fail("rejection signal must not be polled"),
        )

    assert caught.value.record.reason == "resource_pack_rejection_signal"
    assert caught.value.record.rejection_signals == (
        "error_reading_pack_metadata",
        "missing_mandatory_fields",
    )


def test_runtime_gate_fails_on_removed_resource_pack_signal(tmp_path: Path) -> None:
    _write_log(
        tmp_path,
        "[Render thread/WARN]: Removed resource pack file/alpha.zip from options",
        _reload(),
    )

    with pytest.raises(ResourcePackRuntimeError) as caught:
        validate_resourcepack_runtime(tmp_path, expected_filenames=["alpha.zip"], timeout_sec=0)

    assert caught.value.record.rejection_signals == ("removed_resource_pack",)


def test_runtime_gate_requires_empty_actual_set_for_empty_expected_set(tmp_path: Path) -> None:
    _write_log(tmp_path, _reload())
    record = validate_resourcepack_runtime(tmp_path, expected_filenames=[], timeout_sec=0)
    assert record.status == "pass"
    assert record.actual_file_packs == ()

    _write_log(tmp_path, _reload("file/leftover.zip"))
    with pytest.raises(ResourcePackRuntimeError) as caught:
        validate_resourcepack_runtime(tmp_path, expected_filenames=[], timeout_sec=0)
    assert caught.value.record.unexpected_file_packs == ("file/leftover.zip",)


def test_runtime_gate_uses_only_last_reload_line(tmp_path: Path) -> None:
    first = _reload("file/old.zip")
    last = _reload("file/current.zip")
    _write_log(tmp_path, first, "[Render thread/INFO]: Finishing reload", last)

    record = inspect_resourcepack_runtime(tmp_path, expected_filenames=["current.zip"])

    assert record.status == "pass"
    assert record.reload_line == last
    assert record.actual_file_packs == ("file/current.zip",)


def test_runtime_gate_rejects_different_pack_priority_order(tmp_path: Path) -> None:
    _write_log(tmp_path, _reload("file/beta.zip", "file/alpha.zip"))

    with pytest.raises(ResourcePackRuntimeError) as caught:
        validate_resourcepack_runtime(
            tmp_path,
            expected_filenames=["alpha.zip", "beta.zip"],
            timeout_sec=0,
        )

    assert caught.value.record.reason == "timeout:resource_pack_order_mismatch"
    assert caught.value.record.missing_file_packs == ()
    assert caught.value.record.unexpected_file_packs == ()


def test_runtime_gate_rejects_duplicate_active_pack(tmp_path: Path) -> None:
    _write_log(tmp_path, _reload("file/alpha.zip", "file/alpha.zip"))

    with pytest.raises(ResourcePackRuntimeError) as caught:
        validate_resourcepack_runtime(
            tmp_path,
            expected_filenames=["alpha.zip"],
            timeout_sec=0,
        )

    assert caught.value.record.reason == "timeout:resource_pack_duplicate"
    assert caught.value.record.duplicate_file_packs == ("file/alpha.zip",)


def test_runtime_gate_rejects_ambiguous_comma_in_filename(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="cannot contain newlines or commas"):
        validate_resourcepack_runtime(
            tmp_path,
            expected_filenames=["alpha,beta.zip"],
            timeout_sec=0,
        )


def test_runtime_gate_polls_until_reload_line_is_available(tmp_path: Path) -> None:
    responses: list[str | None] = [None, "client starting\n", _reload("file/alpha.zip")]
    now = [10.0]

    def read_log(_path: Path) -> str | None:
        return responses.pop(0)

    def sleep(seconds: float) -> None:
        now[0] += seconds

    record = validate_resourcepack_runtime(
        tmp_path,
        expected_filenames=["alpha.zip"],
        timeout_sec=1.0,
        poll_sec=0.1,
        read_log=read_log,
        monotonic=lambda: now[0],
        sleep=sleep,
    )

    assert record.status == "pass"
    assert record.attempts == 3
    assert record.elapsed_sec == pytest.approx(0.2)


def test_runtime_gate_times_out_without_reload_line(tmp_path: Path) -> None:
    now = [20.0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    with pytest.raises(ResourcePackRuntimeError) as caught:
        validate_resourcepack_runtime(
            tmp_path,
            expected_filenames=[],
            timeout_sec=0.3,
            poll_sec=0.1,
            read_log=lambda _path: "client still warming up\n",
            monotonic=lambda: now[0],
            sleep=sleep,
        )

    assert caught.value.record.status == "fail"
    assert caught.value.record.reason == "timeout:reload_line_missing"
    assert caught.value.record.attempts == 4
    assert caught.value.record.elapsed_sec == pytest.approx(0.3)
