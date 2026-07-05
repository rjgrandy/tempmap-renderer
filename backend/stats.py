from __future__ import annotations

import resource
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict

# Lightweight in-process performance counters, exposed via
# GET /api/debug/stats to make CPU usage attributable without a profiler.

_lock = threading.Lock()
_started = time.time()
_counters: Dict[str, int] = defaultdict(int)
_totals: Dict[str, float] = defaultdict(float)
_recent: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=200))


def incr(name: str, amount: int = 1) -> None:
    with _lock:
        _counters[name] += amount


def observe(name: str, seconds: float) -> None:
    with _lock:
        _counters[f"{name}.count"] += 1
        _totals[name] += seconds
        _recent[name].append(seconds)


class timed:
    """Context manager that records wall-clock duration under a name."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __enter__(self) -> "timed":
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *_exc) -> None:
        observe(self.name, time.perf_counter() - self._t0)


def snapshot() -> Dict:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu_seconds = usage.ru_utime + usage.ru_stime
    child_cpu_seconds = children.ru_utime + children.ru_stime
    uptime = max(time.time() - _started, 1e-6)
    with _lock:
        timings = {}
        for name, total in _totals.items():
            values = list(_recent[name])
            timings[name] = {
                "count": _counters.get(f"{name}.count", 0),
                "total_seconds": round(total, 2),
                "percent_of_one_core": round(100.0 * total / uptime, 2),
                "recent_mean_seconds": round(sum(values) / len(values), 4) if values else 0.0,
                "recent_max_seconds": round(max(values), 4) if values else 0.0,
            }
        counters = {
            name: count for name, count in _counters.items() if not name.endswith(".count")
        }
    return {
        "process": {
            "uptime_seconds": round(uptime, 1),
            "cpu_seconds_total": round(cpu_seconds, 2),
            "avg_percent_of_one_core": round(100.0 * cpu_seconds / uptime, 2),
            "child_cpu_seconds_total": round(child_cpu_seconds, 2),
            "child_avg_percent_of_one_core": round(100.0 * child_cpu_seconds / uptime, 2),
        },
        "counters": counters,
        "timings": timings,
    }
