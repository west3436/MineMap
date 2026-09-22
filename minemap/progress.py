"""Progress reporting shared by every pipeline step.

A `Reporter` is passed down into long-running functions. Steps call `log()` for
human-readable lines and `progress()` for a 0..1 fraction. `check_cancel()` raises
`Cancelled` if the user pressed stop. The server wires a Reporter to an SSE stream;
the CLI wires it to stdout; tests can use `NullReporter`.
"""
from __future__ import annotations

import threading
import time
from typing import Callable


class Cancelled(Exception):
    """Raised inside a pipeline step when the user cancelled the job."""


class Reporter:
    def __init__(self, on_log: Callable[[str], None] | None = None,
                 on_progress: Callable[[str, float], None] | None = None):
        self._on_log = on_log or (lambda s: print(s, flush=True))
        self._on_progress = on_progress or (lambda step, frac: None)
        self._cancel = threading.Event()
        self.step = ""
        self._last_progress_t = 0.0

    def set_step(self, name: str) -> None:
        self.step = name
        self._on_progress(name, 0.0)

    def log(self, msg: str) -> None:
        self._on_log(msg)

    def progress(self, frac: float, throttle: float = 0.15) -> None:
        now = time.monotonic()
        if frac >= 1.0 or now - self._last_progress_t >= throttle:
            self._last_progress_t = now
            self._on_progress(self.step, max(0.0, min(1.0, frac)))

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def check_cancel(self) -> None:
        if self._cancel.is_set():
            raise Cancelled(self.step or "job")


class NullReporter(Reporter):
    def __init__(self):
        super().__init__(on_log=lambda s: None, on_progress=lambda a, b: None)
