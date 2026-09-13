"""System-usage sampling for the browse benchmarks.

A tiny background sampler that, while a scenario runs, polls:

  * GPU utilization % and VRAM used (MiB)  — via ``nvidia-smi``
  * system CPU %                            — via ``psutil``
  * system RAM used (GiB)                   — via ``psutil``

and reports peak + average for each. The point is to make the model-choice
decision on numbers, not vibes: a model that's 5% more accurate but pins the
8 GB card at its VRAM ceiling (spilling to slow shared memory) or drives CPU to
100% is not obviously the better pick on this hardware.

Usage:

    with ResourceSampler() as rs:
        ... run one scenario ...
    stats = rs.stats()   # dict of {metric: {"peak": x, "avg": y}} (+ n_samples)

Everything is best-effort: if ``nvidia-smi`` isn't on PATH the GPU fields come
back ``None`` rather than raising, so the harness still runs on a CPU-only box.
"""

from __future__ import annotations

import subprocess
import threading
from typing import Any

try:
    import psutil
except Exception:  # noqa: BLE001
    psutil = None  # type: ignore[assignment]


def _nvidia_smi_once() -> tuple[float, float] | None:
    """Return (gpu_util_pct, vram_used_mib) for GPU 0, or None if unavailable."""
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used",
                "--format=csv,noheader,nounits",
                "--id=0",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode != 0:
            return None
        first = out.stdout.strip().splitlines()[0]
        util_s, mem_s = (p.strip() for p in first.split(","))
        return float(util_s), float(mem_s)
    except Exception:  # noqa: BLE001
        return None


class ResourceSampler:
    """Background thread that samples GPU/CPU/RAM at a fixed interval."""

    def __init__(self, interval: float = 0.5) -> None:
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._gpu_util: list[float] = []
        self._vram: list[float] = []
        self._cpu: list[float] = []
        self._ram: list[float] = []
        self._gpu_available = False

    def _loop(self) -> None:
        # Prime psutil's cpu_percent so the first real reading isn't 0.0.
        if psutil is not None:
            psutil.cpu_percent(interval=None)
        while not self._stop.is_set():
            g = _nvidia_smi_once()
            if g is not None:
                self._gpu_available = True
                self._gpu_util.append(g[0])
                self._vram.append(g[1])
            if psutil is not None:
                self._cpu.append(psutil.cpu_percent(interval=None))
                self._ram.append(psutil.virtual_memory().used / (1024**3))
            self._stop.wait(self.interval)

    def __enter__(self) -> ResourceSampler:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    @staticmethod
    def _pk_avg(xs: list[float], nd: int = 1) -> dict[str, float] | None:
        if not xs:
            return None
        return {"peak": round(max(xs), nd), "avg": round(sum(xs) / len(xs), nd)}

    def stats(self) -> dict[str, Any]:
        return {
            "n_samples": max(len(self._gpu_util), len(self._cpu)),
            "gpu_available": self._gpu_available,
            "gpu_util_pct": self._pk_avg(self._gpu_util),
            "vram_used_mib": self._pk_avg(self._vram, nd=0),
            "cpu_pct": self._pk_avg(self._cpu),
            "ram_used_gib": self._pk_avg(self._ram, nd=2),
        }


def fmt_stats(s: dict[str, Any]) -> str:
    """One-line human summary of a stats dict (peaks), for the results table."""
    if not s:
        return ""
    parts = []
    v = s.get("vram_used_mib")
    if v:
        parts.append(f"vram={v['peak']:.0f}MiB")
    g = s.get("gpu_util_pct")
    if g:
        parts.append(f"gpu={g['peak']:.0f}%")
    c = s.get("cpu_pct")
    if c:
        parts.append(f"cpu={c['peak']:.0f}%")
    r = s.get("ram_used_gib")
    if r:
        parts.append(f"ram={r['peak']:.1f}G")
    return " ".join(parts)
