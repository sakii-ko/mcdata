from __future__ import annotations

import grp
import os
from pathlib import Path
import shutil
import subprocess
from dataclasses import dataclass

from rich.console import Console
from rich.table import Table

console = Console()


@dataclass(frozen=True)
class Check:
    name: str
    value: str
    ok: bool
    note: str = ""


def run_doctor() -> list[Check]:
    checks = [
        _command("python", "python"),
        _command("portablemc", "portablemc"),
        _command("java", "java", required=False),
        _command("ffmpeg", "ffmpeg", required=False),
        _command("tmux", "tmux", required=False),
        _command("xdotool", "xdotool", required=False),
        _command("glxinfo", "glxinfo", required=False),
        _command("Xvfb", "Xvfb", required=False),
        _display(),
        _opengl(),
        _nvidia(),
        _nvidia_devices(),
        _xwrapper(),
        _tmp_for_xorg(),
        _linux_groups(),
    ]
    table = Table(title="mcdata doctor")
    table.add_column("Check")
    table.add_column("Value")
    table.add_column("Status")
    table.add_column("Note")
    for check in checks:
        table.add_row(check.name, check.value, "ok" if check.ok else "missing", check.note)
    console.print(table)
    return checks


def _command(name: str, binary: str, *, required: bool = True) -> Check:
    path = shutil.which(binary)
    return Check(name, path or "-", bool(path) or not required, "required" if required else "optional")


def _display() -> Check:
    display = os.environ.get("DISPLAY")
    return Check("DISPLAY", display or "-", bool(display), "required for real rendering")


def _nvidia() -> Check:
    if not shutil.which("nvidia-smi"):
        return Check("nvidia-smi", "-", False, "NVIDIA GPU info unavailable")
    try:
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
            check=True,
            text=True,
            capture_output=True,
            timeout=10,
        )
        return Check("GPU", res.stdout.strip().replace("\n", "; "), True, "")
    except Exception as exc:
        return Check("GPU", "-", False, str(exc))


def _opengl() -> Check:
    display = os.environ.get("DISPLAY")
    if not display:
        return Check("OpenGL", "-", False, "DISPLAY is not set")
    if not shutil.which("glxinfo"):
        return Check("OpenGL", "-", False, "install mesa-utils/glxinfo")
    try:
        res = subprocess.run(
            ["glxinfo", "-B"],
            check=False,
            text=True,
            capture_output=True,
            timeout=10,
        )
    except Exception as exc:
        return Check("OpenGL", "-", False, str(exc))
    if res.returncode != 0:
        return Check("OpenGL", "-", False, _single_line(res.stderr or res.stdout))
    renderer = _extract_glx_field(res.stdout, "OpenGL renderer string")
    vendor = _extract_glx_field(res.stdout, "OpenGL vendor string")
    direct = _extract_glx_field(res.stdout, "direct rendering")
    value = renderer or vendor or "unknown"
    software = "llvmpipe" in value.lower() or "softpipe" in value.lower()
    nvidia = "nvidia" in f"{renderer} {vendor}".lower()
    if nvidia and not software:
        return Check("OpenGL", value, True, f"NVIDIA-backed; direct={direct or '?'}")
    if software:
        return Check("OpenGL", value, False, "software renderer; smoke tests only")
    return Check("OpenGL", value, False, f"not NVIDIA-backed; direct={direct or '?'}")


def _extract_glx_field(text: str, field: str) -> str:
    prefix = f"{field}:"
    for line in text.splitlines():
        if line.strip().startswith(prefix):
            return line.split(":", 1)[1].strip()
    return ""


def _single_line(text: str) -> str:
    return " ".join(text.strip().split())[:100]


def _nvidia_devices() -> Check:
    devices = [Path("/dev/nvidiactl"), Path("/dev/nvidia0")]
    missing = [str(path) for path in devices if not path.exists()]
    if missing:
        return Check("NVIDIA devices", ", ".join(missing), False, "device nodes missing")
    unreadable = [str(path) for path in devices if not os.access(path, os.R_OK | os.W_OK)]
    if unreadable:
        return Check("NVIDIA devices", ", ".join(unreadable), False, "not readable/writable")
    return Check("NVIDIA devices", "/dev/nvidiactl,/dev/nvidia0", True, "")


def _xwrapper() -> Check:
    config = Path("/etc/X11/Xwrapper.config")
    if os.geteuid() == 0:
        return Check("Xorg wrapper", "root", True, "root can start Xorg directly")
    if not config.exists():
        return Check("Xorg wrapper", "-", False, "missing Xwrapper config")
    text = config.read_text(encoding="utf-8", errors="replace")
    allowed = "unknown"
    for line in text.splitlines():
        if line.strip().startswith("allowed_users="):
            allowed = line.split("=", 1)[1].strip()
            break
    ok = allowed == "anybody"
    note = "non-console SSH users need allowed_users=anybody or root-launched Xorg"
    return Check("Xorg wrapper", f"allowed_users={allowed}", ok, "" if ok else note)


def _tmp_for_xorg() -> Check:
    path = Path("/tmp")
    try:
        usage = shutil.disk_usage(path)
    except OSError as exc:
        return Check("/tmp for Xorg", "-", False, str(exc))
    free_mb = usage.free // (1024 * 1024)
    probe = path / f".mcdata-write-probe-{os.getpid()}"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        return Check("/tmp for Xorg", f"{free_mb} MiB free", False, str(exc))
    ok = free_mb >= 64
    note = "Xorg creates /tmp/.X*-lock and /tmp/.X11-unix sockets"
    return Check("/tmp for Xorg", f"{free_mb} MiB free", ok, "" if ok else note)


def _linux_groups() -> Check:
    names = _current_group_names()
    value = ",".join(names) if names else "-"
    if os.geteuid() == 0:
        return Check("Linux groups", value, True, "root")
    needed = {"video", "render"}
    have = needed.intersection(names)
    ok = bool(have)
    note = "video/render group is usually needed for /dev/dri render nodes"
    return Check("Linux groups", value, ok, "" if ok else note)


def _current_group_names() -> list[str]:
    gids = {os.getgid(), *os.getgroups()}
    names: list[str] = []
    for gid in sorted(gids):
        try:
            names.append(grp.getgrgid(gid).gr_name)
        except KeyError:
            names.append(str(gid))
    return names
