"""Periodic assembly-state maintenance for the telemetry store.

Open traces silent longer than the quiet window become 'quiet' without
waiting for a reader. A daemon thread ticks SpanStore.advance_assembly;
the loop exits by itself when the store closes or breaks.
"""

from __future__ import annotations

import threading
import time
from typing import Any

QUIET_AFTER_NS = 5_000_000_000


class AssemblyTicker:
    def __init__(
        self,
        store: Any,
        *,
        interval_s: float = 1.0,
        quiet_after_ns: int = QUIET_AFTER_NS,
    ):
        self._store = store
        self._interval = interval_s
        self._quiet_after = quiet_after_ns
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> AssemblyTicker:
        if self._thread is not None:
            return self
        self._thread = threading.Thread(target=self._run, name="assembly-ticker", daemon=True)
        self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self._store.advance_assembly(time.time_ns(), self._quiet_after)
            except Exception:
                # Store closed or broken: quiet exit (daemon thread).
                return

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2)
