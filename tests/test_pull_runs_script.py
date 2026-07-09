from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pull_runs_from_remote.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _run_pull(
    tmp_path: Path,
    *,
    ssh_script: str,
    rsync_script: str = "#!/usr/bin/env bash\nexit 0\n",
) -> tuple[subprocess.CompletedProcess[str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(bin_dir / "ssh", ssh_script)
    _write_executable(bin_dir / "rsync", rsync_script)

    remote_dir = tmp_path / "remote"
    remote_dir.mkdir()
    (remote_dir / "run.txt").write_text("data\n", encoding="utf-8")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "MCDATA_OUTPUT_DIR": str(tmp_path / "local"),
    }
    result = subprocess.run(
        ["bash", str(SCRIPT), "render-host", str(remote_dir), "--purge"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    return result, remote_dir


def test_purge_guard_does_not_match_its_remote_shell(tmp_path: Path) -> None:
    result, remote_dir = _run_pull(
        tmp_path,
        ssh_script=(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "shift\n"
            "bash -c \"$1\"\n"
        ),
    )

    assert result.returncode == 0, result.stderr
    assert list(remote_dir.iterdir()) == []


@pytest.mark.parametrize("guard_rc", [0, 2, 127, 255])
def test_purge_refuses_active_or_failed_process_check(
    tmp_path: Path,
    guard_rc: int,
) -> None:
    result, remote_dir = _run_pull(
        tmp_path,
        ssh_script=(
            "#!/usr/bin/env bash\n"
            "set -u\n"
            "if [[ \"$2\" == pgrep* ]]; then\n"
            f"  exit {guard_rc}\n"
            "fi\n"
            "exit 99\n"
        ),
    )

    assert result.returncode != 0
    assert (remote_dir / "run.txt").exists()
    if guard_rc == 0:
        assert "pipeline appears active" in result.stderr
    else:
        assert f"active-process check failed on render-host (rc={guard_rc})" in result.stderr


def test_second_rsync_change_refuses_purge_before_process_check(tmp_path: Path) -> None:
    result, remote_dir = _run_pull(
        tmp_path,
        ssh_script="#!/usr/bin/env bash\nexit 88\n",
        rsync_script="#!/usr/bin/env bash\nprintf '>f+++++++++ still-writing\\n'\n",
    )

    assert result.returncode != 0
    assert "second rsync pass still transferred" in result.stderr
    assert (remote_dir / "run.txt").exists()
