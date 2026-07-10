from __future__ import annotations

import json
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from rich.console import Console

console = Console()
MOVEMENT_KEYS = ("w", "a", "s", "d", "space", "left_shift")


class StartEvent(Protocol):
    def wait(self, timeout: float | None = None) -> bool: ...


class StopEvent(Protocol):
    def is_set(self) -> bool: ...


class SemanticEventExecutor(Protocol):
    def dispatch(self, event: dict[str, Any], send_input: Callable[[dict[str, Any]], bool]) -> dict[str, Any]: ...


class InputController:
    """Stateful keyboard/mouse control with guaranteed movement-key release."""

    def __init__(
        self,
        *,
        window_name: str = "Minecraft",
        stop_event: StopEvent | None = None,
    ) -> None:
        self._backend = _backend()
        self._stop_event = stop_event
        self._warned: set[tuple[str, ...]] = set()
        self._held: set[str] = set()
        self._closed = False
        if self._backend == "xdotool":
            _focus_window(window_name, warned=self._warned)
        else:
            _xtest_focus_window(window_name)
        self.inherited_keys = _release_inherited_keys(
            self._backend,
            warned=self._warned,
        )

    def key_down(self, key: str) -> None:
        if key in self._held:
            return
        if self._send({"key": key, "action": "down"}):
            self._held.add(key)

    def key_up(self, key: str) -> None:
        if key not in self._held:
            return
        if self._send({"key": key, "action": "up"}, allow_after_stop=True):
            self._held.discard(key)

    def tap(self, key: str) -> None:
        self._send({"key": key, "action": "tap"})

    def move_mouse(self, dx: int, dy: int = 0) -> None:
        if dx or dy:
            self._send({"mouse_dx": int(dx), "mouse_dy": int(dy)})

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._held:
            _release_keys(
                sorted(self._held),
                self._backend,
                warned=self._warned,
            )
            self._held.clear()

    def __enter__(self) -> InputController:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _send(self, event: dict, *, allow_after_stop: bool = False) -> bool:
        if self._closed:
            raise RuntimeError("input controller is closed")
        if _stop_requested(self._stop_event) and not allow_after_stop:
            return False
        if self._backend == "xdotool":
            result = _send_event_xdotool(
                event,
                warned=self._warned,
                stop_event=self._stop_event,
            )
        else:
            result = _send_event_xtest(event, stop_event=self._stop_event)
        return result is not False


def replay_trajectory(
    path: Path,
    *,
    window_name: str = "Minecraft",
    startup_delay: float = 0,
    start_event: StartEvent | None = None,
    stop_event: StopEvent | None = None,
    run_dir: Path | None = None,
    semantic_executor: SemanticEventExecutor | None = None,
    episode_reset_evidence: dict[str, Any] | None = None,
) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    events = sorted(data.get("events", []), key=lambda e: float(e.get("t", 0)))
    if start_event is not None:
        console.print(f"Waiting for capture-ready signal before replaying {len(events)} events...")
        if not _wait_for_start(start_event, stop_event):
            return
    if startup_delay > 0:
        console.print(f"Waiting {startup_delay:.1f}s before replaying {len(events)} events...")
        if not _sleep_interruptible(startup_delay, stop_event):
            return
    backend = _backend()
    xdotool_warnings: set[tuple[str, ...]] = set()
    if backend == "xdotool":
        _focus_window(window_name, warned=xdotool_warnings)
    else:
        _xtest_focus_window(window_name)
    inherited = _release_inherited_keys(backend, warned=xdotool_warnings)
    if inherited:
        console.print(f"Warning: inherited stuck keys: {inherited}")
    replay_log = _ReplayLog(run_dir / "replay_log.jsonl") if run_dir else None
    start = time.monotonic()
    if replay_log is not None:
        replay_log.write_start(start, episode_reset_evidence=episode_reset_evidence)
        if inherited:
            replay_log.write_control("inherited_stuck_keys", keys=inherited)
    held: set[str] = set()
    try:
        for event in events:
            if _stop_requested(stop_event):
                break
            scheduled_t = float(event.get("t", 0))
            target = start + scheduled_t
            if target > time.monotonic() and not _sleep_interruptible(
                target - time.monotonic(),
                stop_event,
            ):
                break
            actual_t = time.monotonic() - start
            execution_status, semantic_evidence = _dispatch_replay_event(
                event,
                backend=backend,
                semantic_executor=semantic_executor,
                warned=xdotool_warnings,
                stop_event=stop_event,
                held=held,
            )
            if replay_log is not None:
                replay_log.write(
                    event=event,
                    scheduled_t=scheduled_t,
                    actual_t=actual_t,
                    execution_status=execution_status,
                    semantic_evidence=semantic_evidence,
                )
    finally:
        released = sorted(held)
        if released:
            _release_keys(released, backend, warned=xdotool_warnings)
            console.print(f"Released held replay keys: {released}")
            if replay_log is not None:
                replay_log.write_control("released_keys", keys=released)
        if replay_log is not None:
            replay_log.close()


def prepare_capture_view(
    *, window_name: str = "Minecraft", hide_hud: bool = True, settle_sec: float = 1.0
) -> None:
    backend = _backend()
    xdotool_warnings: set[tuple[str, ...]] = set()
    if backend == "xdotool":
        _focus_window(window_name, warned=xdotool_warnings)
    else:
        _xtest_focus_window(window_name)
    time.sleep(0.2)
    if hide_hud:
        event = {"key": "f1", "action": "tap"}
        if backend == "xdotool":
            _send_event_xdotool(event, warned=xdotool_warnings)
        else:
            _send_event_xtest(event)
    if settle_sec > 0:
        time.sleep(settle_sec)


def _wait_for_start(start_event: StartEvent, stop_event: StopEvent | None) -> bool:
    while not _stop_requested(stop_event):
        if start_event.wait(0.25):
            return True
    return False


def _sleep_interruptible(seconds: float, stop_event: StopEvent | None) -> bool:
    end = time.monotonic() + max(0.0, seconds)
    while True:
        if _stop_requested(stop_event):
            return False
        remaining = end - time.monotonic()
        if remaining <= 0:
            return True
        time.sleep(min(0.25, remaining))


def _stop_requested(stop_event: StopEvent | None) -> bool:
    return bool(stop_event is not None and stop_event.is_set())


def _update_held(held: set[str], event: dict) -> None:
    if "key" not in event:
        return
    key = str(event["key"])
    action = str(event.get("action", "tap"))
    if action == "down":
        held.add(key)
    elif action in {"up", "tap"}:
        held.discard(key)


def append_replay_control(
    path: Path, name: str, *, semantic_evidence: dict[str, Any]
) -> None:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": {"replay_control": name},
        "semantic_evidence": semantic_evidence,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
        handle.flush()


def _dispatch_replay_event(
    event: dict[str, Any],
    *, backend: str, semantic_executor: SemanticEventExecutor | None,
    warned: set[tuple[str, ...]], stop_event: StopEvent | None, held: set[str],
) -> tuple[str, dict[str, Any] | None]:
    execution_status = _event_execution_status(event, semantic_executor=semantic_executor)
    if execution_status == "input_dispatched_pending_probe":
        if semantic_executor is None:
            raise RuntimeError("semantic input executor disappeared before dispatch")
        evidence = semantic_executor.dispatch(
            event,
            lambda primitive: _send_backend_event(
                backend,
                primitive,
                warned=warned,
                stop_event=stop_event,
            ),
        )
        return execution_status, evidence
    if execution_status != "unsupported_contract_only":
        dispatched = _send_backend_event(
            backend,
            event,
            warned=warned,
            stop_event=stop_event,
        )
        camera_family = _advanced_camera_family(event)
        if dispatched is False and camera_family is not None:
            family, phase = camera_family
            raise RuntimeError(f"{family} camera {phase} input dispatch failed")
        if dispatched is False and event.get("semantic_action") == "deliberate_jump":
            phase = str(event.get("semantic_phase", "unknown"))
            raise RuntimeError(f"Deliberate jump {phase} input dispatch failed")
        _update_held(held, event)
    return execution_status, None


def _event_execution_status(event: dict, *, semantic_executor: SemanticEventExecutor | None = None) -> str:
    if event.get("semantic_action") in {
        "deterministic_block_placement",
        "controlled_combat",
    }:
        if semantic_executor is not None:
            return "input_dispatched_pending_probe"
        return "unsupported_contract_only"
    if "key" in event or "mouse_dx" in event or "mouse_dy" in event or "mouse_button" in event:
        return "executed"
    return "non_input"


def _advanced_camera_family(event: dict) -> tuple[str, str] | None:
    for prefix, label in (("placement", "Placement"), ("combat", "Combat")):
        if event.get(f"{prefix}_aim") is True:
            return label, "aim"
        if event.get(f"{prefix}_aim_restore") is True:
            return label, "restore"
    return None


def _focus_window(window_name: str, *, warned: set[tuple[str, ...]] | None = None) -> None:
    _run_xdotool(["search", "--name", window_name, "windowactivate"], warned=warned)


def _backend() -> str:
    if shutil.which("xdotool"):
        return "xdotool"
    try:
        import Xlib  # noqa: F401
        import Xlib.ext.xtest  # noqa: F401

        return "xtest"
    except Exception as exc:
        raise RuntimeError("action replay requires xdotool or python-xlib with XTEST") from exc


def _send_event_xdotool(
    event: dict,
    *,
    warned: set[tuple[str, ...]] | None = None,
    stop_event: StopEvent | None = None,
) -> bool:
    success = True
    if "key" in event:
        key = str(event["key"])
        action = event.get("action", "tap")
        if action == "down":
            success = _run_xdotool(["keydown", key], warned=warned) and success
        elif action == "up":
            success = _run_xdotool(["keyup", key], warned=warned) and success
        else:
            success = _run_xdotool(["key", key], warned=warned) and success
    if "mouse_button" in event:
        button = str(event["mouse_button"])
        action = event.get("action", "click")
        command = {"down": "mousedown", "up": "mouseup"}.get(action, "click")
        success = _run_xdotool([command, button], warned=warned) and success
    if "mouse_dx" in event or "mouse_dy" in event:
        for dx, dy, delay in _mouse_steps(event):
            if _stop_requested(stop_event):
                return False
            success = (
                _run_xdotool(["mousemove_relative", "--", str(dx), str(dy)], warned=warned)
                and success
            )
            if delay > 0 and not _sleep_interruptible(delay, stop_event):
                return False
    return success


def _run_xdotool(args: list[str], *, warned: set[tuple[str, ...]] | None = None) -> bool:
    cmd = ["xdotool", *args]
    result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if result.returncode == 0:
        return True
    key = tuple(cmd[:2])
    if warned is not None and key in warned:
        return False
    if warned is not None:
        warned.add(key)
    detail = (result.stderr or result.stdout).strip()
    suffix = f": {detail}" if detail else ""
    console.print(
        f"Warning: xdotool command failed ({' '.join(cmd)}), rc={result.returncode}{suffix}"
    )
    return False


def _xtest_focus_window(window_name: str) -> None:
    try:
        from Xlib import X
        from Xlib.display import Display
    except Exception:
        return
    display = Display()
    root = display.screen().root
    for window in _walk_windows(root):
        try:
            name = window.get_wm_name() or ""
            if window_name.lower() in name.lower():
                window.set_input_focus(X.RevertToPointerRoot, X.CurrentTime)
                window.configure(stack_mode=X.Above)
                display.sync()
                return
        except Exception:
            continue


def _walk_windows(window):
    try:
        children = window.query_tree().children
    except Exception:
        return
    for child in children:
        yield child
        yield from _walk_windows(child)


def _send_event_xtest(event: dict, *, stop_event: StopEvent | None = None) -> bool:
    from Xlib import X
    from Xlib.display import Display
    from Xlib.ext import xtest

    display = Display()
    success = True
    if "key" in event:
        keycode = _keycode(display, str(event["key"]))
        action = event.get("action", "tap")
        if keycode:
            if action == "down":
                xtest.fake_input(display, X.KeyPress, keycode)
            elif action == "up":
                xtest.fake_input(display, X.KeyRelease, keycode)
            else:
                xtest.fake_input(display, X.KeyPress, keycode)
                xtest.fake_input(display, X.KeyRelease, keycode)
        else:
            console.print(f"Warning: could not resolve keycode for {event['key']!r}")
            success = False
    if "mouse_button" in event:
        button = int(event["mouse_button"])
        action = event.get("action", "click")
        if action in {"down", "click"}:
            xtest.fake_input(display, X.ButtonPress, button)
        if action in {"up", "click"}:
            xtest.fake_input(display, X.ButtonRelease, button)
    if "mouse_dx" in event or "mouse_dy" in event:
        for dx, dy, delay in _mouse_steps(event):
            if _stop_requested(stop_event):
                return False
            xtest.fake_input(display, X.MotionNotify, x=dx, y=dy)
            display.sync()
            if delay > 0 and not _sleep_interruptible(delay, stop_event):
                return False
    display.sync()
    return success


def _send_backend_event(
    backend: str,
    event: dict[str, Any],
    *, warned: set[tuple[str, ...]], stop_event: StopEvent | None,
) -> bool:
    if backend == "xdotool":
        return _send_event_xdotool(event, warned=warned, stop_event=stop_event)
    return _send_event_xtest(event, stop_event=stop_event)


def _release_inherited_keys(
    backend: str,
    *,
    warned: set[tuple[str, ...]] | None = None,
) -> list[str]:
    if backend == "xdotool":
        for key in MOVEMENT_KEYS:
            _release_key_xdotool(key, warned=warned)
        return []
    return _release_pressed_keys_xtest(MOVEMENT_KEYS)


def _release_keys(
    keys: list[str],
    backend: str,
    *,
    warned: set[tuple[str, ...]] | None = None,
) -> None:
    try:
        if backend == "xdotool":
            for key in keys:
                _release_key_xdotool(key, warned=warned)
        else:
            _release_keys_xtest(keys)
    except Exception as exc:
        console.print(f"Warning: failed to release held replay keys {keys}: {exc}")


def _release_key_xdotool(key: str, *, warned: set[tuple[str, ...]] | None = None) -> None:
    _run_xdotool(["keyup", key], warned=warned)


def _release_pressed_keys_xtest(keys: tuple[str, ...]) -> list[str]:
    from Xlib import X
    from Xlib.display import Display
    from Xlib.ext import xtest

    display = Display()
    keymap = display.query_keymap()
    released: list[str] = []
    for key in keys:
        keycode = _keycode(display, key)
        if keycode and _keymap_has_key(keymap, keycode):
            xtest.fake_input(display, X.KeyRelease, keycode)
            released.append(key)
    display.sync()
    return released


def _release_keys_xtest(keys: list[str]) -> None:
    from Xlib import X
    from Xlib.display import Display
    from Xlib.ext import xtest

    display = Display()
    for key in keys:
        keycode = _keycode(display, key)
        if keycode:
            xtest.fake_input(display, X.KeyRelease, keycode)
    display.sync()


def _keymap_has_key(keymap, keycode: int) -> bool:
    byte = keymap[keycode // 8]
    if not isinstance(byte, int):
        byte = ord(byte)
    return bool(byte & (1 << (keycode % 8)))


def _keycode(display, key: str) -> int | None:
    mapping = {
        "left_shift": "Shift_L",
        "shift": "Shift_L",
        "space": "space",
        "f1": "F1",
        "w": "w",
        "a": "a",
        "s": "s",
        "d": "d",
    }
    sym_name = mapping.get(key.lower(), key)
    try:
        from Xlib import XK

        keysym = XK.string_to_keysym(sym_name)
        if keysym:
            return display.keysym_to_keycode(keysym)
    except Exception:
        return None
    return None


def _mouse_steps(event: dict) -> list[tuple[int, int, float]]:
    dx = int(event.get("mouse_dx", 0))
    dy = int(event.get("mouse_dy", 0))
    duration = float(event.get("duration", 0) or 0)
    if duration <= 0:
        return [(dx, dy, 0.0)]
    steps = max(1, min(120, int(duration * 30)))
    result: list[tuple[int, int, float]] = []
    prev_x = 0
    prev_y = 0
    for index in range(1, steps + 1):
        x = round(dx * index / steps)
        y = round(dy * index / steps)
        result.append((x - prev_x, y - prev_y, duration / steps))
        prev_x = x
        prev_y = y
    return result


class _ReplayLog:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("w", encoding="utf-8")

    def write_start(
        self, mono: float, *, episode_reset_evidence: dict[str, Any] | None = None
    ) -> None:
        record = {"event": "start", "mono": mono}
        if episode_reset_evidence is not None:
            record["episode_reset_evidence"] = episode_reset_evidence
        self._fh.write(json.dumps(record, sort_keys=True) + "\n")
        self._fh.flush()

    def write(
        self,
        *,
        event: dict,
        scheduled_t: float,
        actual_t: float,
        execution_status: str,
        semantic_evidence: dict[str, Any] | None,
    ) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "scheduled_t": scheduled_t,
            "actual_t": actual_t,
            "event": event,
            "execution_status": execution_status,
        }
        if semantic_evidence is not None:
            record["semantic_evidence"] = semantic_evidence
        self._fh.write(json.dumps(record, sort_keys=True) + "\n")
        self._fh.flush()

    def write_control(self, name: str, *, keys: list[str]) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": {"replay_control": name, "keys": keys},
        }
        self._fh.write(json.dumps(record, sort_keys=True) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()
