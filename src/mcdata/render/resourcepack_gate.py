from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

RESOURCEPACK_RESOLUTION_SIDECAR = Path(".mcdata/resourcepack-resolution.json")
_RELOAD_MARKER = "Reloading ResourceManager:"
_REJECTION_PATTERNS = (
    ("error_reading_pack_metadata", "error reading pack metadata"),
    ("missing_mandatory_fields", "missing mandatory fields"),
    ("removed_resource_pack", "removed resource pack"),
    ("could_not_load_pack_metadata", "could not load pack metadata"),
    ("could_not_load_pack_metadata", "couldn't load pack metadata"),
)


@dataclass(frozen=True)
class ResourcePackRuntimeRecord:
    status: Literal["pass", "pending", "fail"]
    reason: str | None
    expected_file_packs: tuple[str, ...]
    actual_file_packs: tuple[str, ...]
    missing_file_packs: tuple[str, ...]
    unexpected_file_packs: tuple[str, ...]
    duplicate_file_packs: tuple[str, ...]
    rejection_signals: tuple[str, ...]
    rejection_lines: tuple[str, ...]
    expected_source: str
    log_path: str
    reload_line: str | None
    attempts: int = 1
    elapsed_sec: float = 0.0

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready record suitable for a run manifest or runlog."""
        return {
            "status": self.status,
            "reason": self.reason,
            "expected_file_packs": list(self.expected_file_packs),
            "actual_file_packs": list(self.actual_file_packs),
            "missing_file_packs": list(self.missing_file_packs),
            "unexpected_file_packs": list(self.unexpected_file_packs),
            "duplicate_file_packs": list(self.duplicate_file_packs),
            "rejection_signals": list(self.rejection_signals),
            "rejection_lines": list(self.rejection_lines),
            "expected_source": self.expected_source,
            "log_path": self.log_path,
            "reload_line": self.reload_line,
            "attempts": self.attempts,
            "elapsed_sec": self.elapsed_sec,
        }


class ResourcePackRuntimeError(RuntimeError):
    """Fail-closed runtime resource-pack validation error with structured evidence."""

    def __init__(self, message: str, record: ResourcePackRuntimeRecord) -> None:
        super().__init__(message)
        self.record = record


def expected_resourcepack_ids(
    work_dir: Path,
    *,
    expected_filenames: Sequence[str] | None = None,
    sidecar_path: Path | None = None,
) -> tuple[tuple[str, ...], str]:
    """Resolve expected ``file/<filename>`` pack IDs and their provenance."""
    if expected_filenames is not None:
        filenames = list(expected_filenames)
        source = "explicit"
    else:
        path = sidecar_path or work_dir / RESOURCEPACK_RESOLUTION_SIDECAR
        filenames = _filenames_from_sidecar(path)
        source = str(path)

    identifiers = [_as_file_pack_id(filename) for filename in filenames]
    if len(identifiers) != len(set(identifiers)):
        raise RuntimeError(f"Duplicate expected resource-pack filename from {source}")
    return tuple(identifiers), source


def inspect_resourcepack_runtime(
    work_dir: Path,
    *,
    expected_filenames: Sequence[str] | None = None,
    client_dir: Path | None = None,
    log_path: Path | None = None,
    sidecar_path: Path | None = None,
    read_log: Callable[[Path], str | None] | None = None,
) -> ResourcePackRuntimeRecord:
    """Inspect the latest client log once without waiting or raising on a mismatch."""
    expected, expected_source = expected_resourcepack_ids(
        work_dir,
        expected_filenames=expected_filenames,
        sidecar_path=sidecar_path,
    )
    resolved_log_path = log_path or (client_dir or work_dir) / "logs/latest.log"
    reader = read_log or _read_log_text
    try:
        text = reader(resolved_log_path)
    except OSError:
        text = None

    if text is None:
        return _record(
            status="pending",
            reason="client_log_missing",
            expected=expected,
            expected_source=expected_source,
            log_path=resolved_log_path,
        )

    signals, rejection_lines = _find_rejection_signals(text)
    reload_line = _last_reload_line(text)
    if reload_line is None:
        return _record(
            status="fail" if signals else "pending",
            reason="resource_pack_rejection_signal" if signals else "reload_line_missing",
            expected=expected,
            expected_source=expected_source,
            log_path=resolved_log_path,
            rejection_signals=signals,
            rejection_lines=rejection_lines,
        )

    actual = _file_pack_ids_from_reload_line(reload_line)
    missing = tuple(sorted(set(expected) - set(actual)))
    unexpected = tuple(sorted(set(actual) - set(expected)))
    duplicates = _duplicates(actual)
    if signals:
        status: Literal["pass", "pending", "fail"] = "fail"
        reason = "resource_pack_rejection_signal"
    elif duplicates:
        status = "fail"
        reason = "resource_pack_duplicate"
    elif missing or unexpected:
        status = "fail"
        reason = "resource_pack_set_mismatch"
    elif actual != expected:
        status = "fail"
        reason = "resource_pack_order_mismatch"
    else:
        status = "pass"
        reason = None
    return _record(
        status=status,
        reason=reason,
        expected=expected,
        actual=actual,
        missing=missing,
        unexpected=unexpected,
        duplicates=duplicates,
        rejection_signals=signals,
        rejection_lines=rejection_lines,
        expected_source=expected_source,
        log_path=resolved_log_path,
        reload_line=reload_line,
    )


def validate_resourcepack_runtime(
    work_dir: Path,
    *,
    expected_filenames: Sequence[str] | None = None,
    client_dir: Path | None = None,
    log_path: Path | None = None,
    sidecar_path: Path | None = None,
    timeout_sec: float = 10.0,
    poll_sec: float = 0.2,
    read_log: Callable[[Path], str | None] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> ResourcePackRuntimeRecord:
    """Poll for a conclusive runtime pack reload, returning evidence or failing closed."""
    if timeout_sec < 0:
        raise ValueError("timeout_sec must be non-negative")
    if poll_sec <= 0:
        raise ValueError("poll_sec must be positive")

    start = monotonic()
    attempts = 0
    while True:
        attempts += 1
        record = inspect_resourcepack_runtime(
            work_dir,
            expected_filenames=expected_filenames,
            client_dir=client_dir,
            log_path=log_path,
            sidecar_path=sidecar_path,
            read_log=read_log,
        )
        elapsed = max(0.0, monotonic() - start)
        record = replace(record, attempts=attempts, elapsed_sec=elapsed)
        if record.status == "pass":
            return record
        if record.reason == "resource_pack_rejection_signal":
            raise ResourcePackRuntimeError(_failure_message(record), record)
        if elapsed >= timeout_sec:
            timed_out = replace(record, status="fail", reason=f"timeout:{record.reason}")
            raise ResourcePackRuntimeError(_failure_message(timed_out), timed_out)
        sleep(min(poll_sec, timeout_sec - elapsed))


def _filenames_from_sidecar(path: Path) -> list[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"Resource-pack resolution sidecar is missing: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot read resource-pack resolution sidecar: {path}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("packs"), list):
        raise RuntimeError(f"Resource-pack resolution sidecar has invalid packs list: {path}")

    filenames: list[str] = []
    for index, pack in enumerate(payload["packs"]):
        if not isinstance(pack, dict):
            raise RuntimeError(f"Resource-pack sidecar packs[{index}] is not an object: {path}")
        filename = pack.get("filename")
        if not isinstance(filename, str) or not filename:
            effective_path = pack.get("effective_path")
            if isinstance(effective_path, str) and effective_path:
                filename = Path(effective_path).name
        if not isinstance(filename, str) or not filename:
            raise RuntimeError(
                f"Resource-pack sidecar packs[{index}] has no filename/effective_path: {path}"
            )
        filenames.append(filename)
    return filenames


def _as_file_pack_id(filename: str) -> str:
    if not isinstance(filename, str) or not filename:
        raise RuntimeError("Expected resource-pack filenames must be non-empty strings")
    if "\n" in filename or "\r" in filename or "," in filename:
        raise RuntimeError("Expected resource-pack filenames cannot contain newlines or commas")
    return filename if filename.startswith("file/") else f"file/{filename}"


def _read_log_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return None


def _last_reload_line(text: str) -> str | None:
    last: str | None = None
    for line in text.splitlines():
        if _RELOAD_MARKER in line:
            last = line
    return last


def _file_pack_ids_from_reload_line(line: str) -> tuple[str, ...]:
    payload = line.split(_RELOAD_MARKER, 1)[1]
    identifiers = [item.strip() for item in payload.split(",")]
    return tuple(item for item in identifiers if item.startswith("file/"))


def _duplicates(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    return tuple(duplicates)


def _find_rejection_signals(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    signals: list[str] = []
    lines: list[str] = []
    for line in text.splitlines():
        lowered = line.lower()
        for signal, pattern in _REJECTION_PATTERNS:
            if pattern in lowered:
                if signal not in signals:
                    signals.append(signal)
                if line not in lines:
                    lines.append(line)
    return tuple(signals), tuple(lines)


def _record(
    *,
    status: Literal["pass", "pending", "fail"],
    reason: str | None,
    expected: tuple[str, ...],
    expected_source: str,
    log_path: Path,
    actual: tuple[str, ...] = (),
    missing: tuple[str, ...] = (),
    unexpected: tuple[str, ...] = (),
    duplicates: tuple[str, ...] = (),
    rejection_signals: tuple[str, ...] = (),
    rejection_lines: tuple[str, ...] = (),
    reload_line: str | None = None,
) -> ResourcePackRuntimeRecord:
    return ResourcePackRuntimeRecord(
        status=status,
        reason=reason,
        expected_file_packs=expected,
        actual_file_packs=actual,
        missing_file_packs=missing,
        unexpected_file_packs=unexpected,
        duplicate_file_packs=duplicates,
        rejection_signals=rejection_signals,
        rejection_lines=rejection_lines,
        expected_source=expected_source,
        log_path=str(log_path),
        reload_line=reload_line,
    )


def _failure_message(record: ResourcePackRuntimeRecord) -> str:
    details = [f"reason={record.reason}", f"log={record.log_path}"]
    if record.missing_file_packs:
        details.append(f"missing={list(record.missing_file_packs)!r}")
    if record.unexpected_file_packs:
        details.append(f"unexpected={list(record.unexpected_file_packs)!r}")
    if record.duplicate_file_packs:
        details.append(f"duplicates={list(record.duplicate_file_packs)!r}")
    if record.rejection_signals:
        details.append(f"signals={list(record.rejection_signals)!r}")
    return "Runtime resource-pack validation failed: " + "; ".join(details)
