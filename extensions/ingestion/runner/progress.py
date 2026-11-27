"""What a run is doing, in its log: each dlt step, and every 30 seconds the memory it uses.

dlt prints nothing until a pipeline finishes, and a run the kernel kills for memory has no chance to
write a traceback. These lines show how far it got and how close it came to its memory limit.
"""

import os
import sys
import threading
import time
from pathlib import Path

from dlt.common.runtime.collector_base import Collector

INTERVAL = 30.0


def gb(size):
    return f"{size / 1000**3:.2f} GB"


def memory_used():
    """This process's resident memory in bytes, or None where /proc is missing."""
    try:
        return int(Path("/proc/self/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return None


def memory_limit():
    """The container's memory limit in bytes (cgroup v2, then v1), or None without one."""
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            value = Path(path).read_text().strip()
        except OSError:
            continue
        if value.isdigit() and int(value) < 1 << 60:
            return int(value)
    return None


def with_memory(text):
    used, limit = memory_used(), memory_limit()
    if used is None:
        return text
    return f"{text}, memory {gb(used)}" + (f" of {gb(limit)}" if limit else "")


class Progress(Collector):
    """A dlt collector that logs when each step starts and ends, and a heartbeat in between."""

    def __init__(self, interval=INTERVAL, stream=None):
        self.interval, self.stream = interval, stream
        self.step, self.started, self.counters = None, None, {}
        self.begun = time.monotonic()
        self.stopped = threading.Event()
        self.thread = None

    def log(self, text):
        stream = self.stream or sys.stdout
        elapsed = int(time.monotonic() - self.begun)
        stream.write(f"[{elapsed // 60}m{elapsed % 60:02d}s] {text}\n")
        stream.flush()

    def update(self, name, inc=1, total=None, inc_total=None, message=None, label=None):
        key = f"{name} ({label})" if label else name
        self.counters[key] = self.counters.get(key, 0) + inc

    def _start(self, step):
        self.step, self.started, self.counters = step, time.monotonic(), {}
        self.log(with_memory(f"{step} started"))
        if self.thread is None:
            self.thread = threading.Thread(target=self.beat, daemon=True)
            self.thread.start()

    def _stop(self):
        counts = ", ".join(f"{name}: {count:,}" for name, count in self.counters.items() if count)
        took = int(time.monotonic() - self.started)
        self.log(with_memory(f"{self.step} finished in {took}s" + (f" ({counts})" if counts else "")))
        self.step = None

    def beat(self):
        while not self.stopped.wait(self.interval):
            if self.step:
                self.log(with_memory(f"{self.step} running"))

    def close(self):
        self.stopped.set()
