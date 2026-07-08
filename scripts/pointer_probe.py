#!/usr/bin/env python3
"""Sample X focus window + pointer position to diagnose input-grab loss.

Usage: pointer_probe.py <out.jsonl> <duration_sec>
Requires python-Xlib; uses $DISPLAY.
"""
import json
import sys
import time

from Xlib.display import Display

out_path, duration = sys.argv[1], float(sys.argv[2])
display = Display()
root = display.screen().root
screen_w = display.screen().width_in_pixels
screen_h = display.screen().height_in_pixels

with open(out_path, "w", encoding="utf-8") as fh:
    fh.write(json.dumps({"screen_w": screen_w, "screen_h": screen_h}) + "\n")
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        try:
            focus = display.get_input_focus().focus
            name = None
            try:
                name = focus.get_wm_name()
            except Exception:
                pass
            if not name:
                try:
                    parent = focus.query_tree().parent
                    name = parent.get_wm_name() if parent else None
                except Exception:
                    pass
            qp = root.query_pointer()
            rec = {
                "t": round(time.monotonic(), 2),
                "focus": name or hex(getattr(focus, "id", 0) or 0),
                "px": qp.root_x,
                "py": qp.root_y,
            }
        except Exception as exc:
            rec = {"t": round(time.monotonic(), 2), "error": str(exc)}
        fh.write(json.dumps(rec) + "\n")
        fh.flush()
        time.sleep(0.3)
