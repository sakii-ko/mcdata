from __future__ import annotations

import requests
from rich.console import Console


USER_AGENT = "mcdata/0.1 (+https://github.com/local/mcdata)"
console = Console()


def get_json(url: str, *, timeout: float = 30.0) -> object:
    res = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    res.raise_for_status()
    return res.json()


def download_file(url: str, dest, *, timeout: float = 60.0, retries: int = 3) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            _download_once(url, tmp, timeout=timeout)
            tmp.replace(dest)
            return
        except Exception as exc:
            last_error = exc
            if tmp.exists():
                tmp.unlink()
            if attempt < retries:
                console.print(f"Download failed ({attempt}/{retries}) for {dest.name}: {exc}; retrying...")
    raise RuntimeError(f"Could not download {dest.name}") from last_error


def _download_once(url: str, tmp, *, timeout: float) -> None:
    with requests.get(url, headers={"User-Agent": USER_AGENT}, stream=True, timeout=(15.0, timeout)) as res:
        res.raise_for_status()
        total = int(res.headers.get("content-length", "0") or "0")
        written = 0
        next_report = 32 * 1024 * 1024
        with tmp.open("wb") as fh:
            for chunk in res.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                fh.write(chunk)
                written += len(chunk)
                if total and written >= next_report:
                    console.print(f"  {tmp.name}: {written // (1024 * 1024)}MB/{total // (1024 * 1024)}MB")
                    next_report += 32 * 1024 * 1024
