from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Protocol

from rich.console import Console

console = Console()


class StartEvent(Protocol):
    def wait(self, timeout: float | None = None) -> bool: ...


def replay_trajectory(
    path: Path,
    *,
    window_name: str = "Minecraft",
    startup_delay: float = 0,
    start_event: StartEvent | None = None,
) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    events = sorted(data.get("events", []), key=lambda e: float(e.get("t", 0)))
    if start_event is not None:
        console.print(f"Waiting for capture-ready signal before replaying {len(events)} events...")
        start_event.wait()
    if startup_delay > 0:
        console.print(f"Waiting {startup_delay:.1f}s before replaying {len(events)} events...")
        time.sleep(startup_delay)
    backend = _backend()
    if backend == "xdotool":
        _focus_window(window_name)
    else:
        _xtest_focus_window(window_name)
    start = time.monotonic()
    for event in events:
        target = start + float(event.get("t", 0))
        if target > time.monotonic():
            time.sleep(target - time.monotonic())
        if backend == "xdotool":
            _send_event_xdotool(event)
        else:
            _send_event_xtest(event)


def prepare_capture_view(*, window_name: str = "Minecraft", hide_hud: bool = True, settle_sec: float = 1.0) -> None:
    backend = _backend()
    if backend == "xdotool":
        _focus_window(window_name)
    else:
        _xtest_focus_window(window_name)
    time.sleep(0.2)
    if hide_hud:
        event = {"key": "f1", "action": "tap"}
        if backend == "xdotool":
            _send_event_xdotool(event)
        else:
            _send_event_xtest(event)
    if settle_sec > 0:
        time.sleep(settle_sec)


def _focus_window(window_name: str) -> None:
    subprocess.run(["xdotool", "search", "--name", window_name, "windowactivate"], check=False)


def _backend() -> str:
    if shutil.which("xdotool"):
        return "xdotool"
    try:
        import Xlib  # noqa: F401
        import Xlib.ext.xtest  # noqa: F401

        return "xtest"
    except Exception as exc:
        raise RuntimeError("action replay requires xdotool or python-xlib with XTEST") from exc


def _send_event_xdotool(event: dict) -> None:
    if "key" in event:
        key = str(event["key"])
        action = event.get("action", "tap")
        if action == "down":
            subprocess.run(["xdotool", "keydown", key], check=False)
        elif action == "up":
            subprocess.run(["xdotool", "keyup", key], check=False)
        else:
            subprocess.run(["xdotool", "key", key], check=False)
    if "mouse_dx" in event or "mouse_dy" in event:
        for dx, dy, delay in _mouse_steps(event):
            subprocess.run(["xdotool", "mousemove_relative", "--", str(dx), str(dy)], check=False)
            if delay > 0:
                time.sleep(delay)


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


def _send_event_xtest(event: dict) -> None:
    from Xlib import X
    from Xlib.display import Display
    from Xlib.ext import xtest

    display = Display()
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
    if "mouse_dx" in event or "mouse_dy" in event:
        for dx, dy, delay in _mouse_steps(event):
            xtest.fake_input(display, X.MotionNotify, x=dx, y=dy)
            display.sync()
            if delay > 0:
                time.sleep(delay)
    display.sync()


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
