"""Live conversion progress: phases, micro-steps, warnings, notes.

The converter is instrumented with a :class:`Reporter` that every stage
(pack reading, chunk decryption, image decoding, event transpiling, zip
packing) calls into.  Each call emits a JSON-serialisable *snapshot* event
through the reporter's ``sink``, so the CLI, the web UI and the Electron
app can all render the exact same animated progress without knowing the
converter's internals.

Event shape (every event carries the full state, so renderers are dumb):

    {
      "type": "phase" | "progress" | "warn" | "note" | "done",
      "phase": "images",          # current phase id
      "phase_title": "Decoding images",   # human readable phase title
      "step": "image 42 of 1200 (sprite_09.png)",  # micro-step description
      "done": 42, "total": 1200,  # progress within the phase
      "pct": 3.5,                 # 0..100 within the phase
      "overall": 61.2,            # 0..100 across all phases
      "elapsed": 1.234,
      "warnings": [...],          # accumulated (bounded)
      "notes": [...],             # accumulated (bounded)
    }
"""
from __future__ import annotations

import json
import threading
import time
from typing import Callable, Dict, List, Optional

# Each phase contributes this fraction of the overall bar (sums to 1.0).
# Order must match execution order so the bar only ever moves forward.
_PHASE_WEIGHTS: Dict[str, float] = {
    "read": 0.04,
    "detect": 0.03,
    "pack": 0.05,
    "gamedata": 0.12,
    "chunks": 0.10,
    "objects": 0.10,
    "frames": 0.08,
    "images": 0.18,
    "sounds": 0.04,
    "events": 0.08,
    "build": 0.06,
    "transpile": 0.08,
    "zip": 0.04,
}

PHASE_ORDER = list(_PHASE_WEIGHTS.keys())


def _default_phase_title(phase: str) -> str:
    return {
        "detect": "Detecting input format",
        "read": "Reading input file",
        "pack": "Reading EXE pack",
        "gamedata": "Reading game data",
        "chunks": "Decrypting game chunks",
        "objects": "Parsing objects",
        "frames": "Parsing frames",
        "images": "Decoding images to PNG",
        "sounds": "Extracting sounds",
        "events": "Parsing event lists",
        "transpile": "Compiling events to Scratch blocks",
        "build": "Building Scratch project",
        "zip": "Packing .sb3 archive",
    }.get(phase, phase)


class Reporter:
    """Collects and streams conversion progress snapshots.

    ``sink(event_dict)`` is called synchronously for every event.  The
    reporter is thread-safe: the web UI converts in a worker thread while
    an SSE handler thread reads events.
    """

    def __init__(self, title: str = "Converting", sink: Optional[Callable[[dict], None]] = None,
                 max_history: int = 400):
        self.title = title
        self.sink = sink
        self._lock = threading.Lock()
        self._t0 = time.monotonic()
        self.phase_id: Optional[str] = None
        self.phase_title: str = ""
        self._step_text: str = ""
        self.done: int = 0
        self.total: int = 1
        self.warnings: List[str] = []
        self.notes: List[str] = []
        self.history: List[dict] = []          # bounded event replay for SSE
        self._max_history = max_history
        self._finished = False
        self.stats: dict = {}

    # -- internals ----------------------------------------------------------

    def snapshot(self) -> dict:
        with self._lock:
            return self._snapshot_locked()

    def _snapshot_locked(self) -> dict:
        # Clamp: a phase tick can momentarily run ahead of its total when a
        # nested sub-phase (image/sound bank) swapped in a smaller total;
        # the bar must never show e.g. 4200%.
        pct = (self.done / self.total * 100.0) if self.total > 0 else 0.0
        pct = min(max(pct, 0.0), 100.0)
        overall = 0.0
        if self.phase_id:
            order = PHASE_ORDER
            if self.phase_id in order:
                idx = order.index(self.phase_id)
                overall = sum(_PHASE_WEIGHTS[p] for p in order[:idx])
                overall += _PHASE_WEIGHTS[self.phase_id] * (pct / 100.0)
        return {
            "title": self.title,
            "type": "progress",
            "phase": self.phase_id,
            "phase_title": self.phase_title or _default_phase_title(self.phase_id or ""),
            "step": self._step_text,
            "done": self.done,
            "total": self.total,
            "pct": round(pct, 1),
            "overall": round(overall * 100.0, 1),
            "elapsed": round(time.monotonic() - self._t0, 2),
            "warnings": list(self.warnings[-self._max_history:]),
            "notes": list(self.notes[-self._max_history:]),
        }

    def _emit(self, etype: str, **extra) -> dict:
        with self._lock:
            ev = self._snapshot_locked()
            ev["type"] = etype
            ev.update(extra)
            self.history.append(ev)
            if len(self.history) > self._max_history:
                del self.history[: len(self.history) - self._max_history]
        if self.sink:
            try:
                self.sink(ev)
            except Exception:  # noqa: BLE001 - a broken sink must not kill conversion
                pass
        return ev

    # -- API ----------------------------------------------------------------

    def phase(self, phase_id: str, title: Optional[str] = None,
              total: int = 1, step: Optional[str] = None) -> None:
        with self._lock:
            self.phase_id = phase_id
            self.phase_title = title or _default_phase_title(phase_id)
            self.done = 0
            self.total = max(int(total), 1)
            self._step_text = step or self.phase_title
        self._emit("phase")

    def step(self, text: str) -> None:
        with self._lock:
            self._step_text = text
        self._emit("progress")

    def tick(self, done: Optional[int] = None, total: Optional[int] = None,
             step: Optional[str] = None) -> None:
        with self._lock:
            if done is not None:
                self.done = int(done)
            else:
                self.done += 1
            if total is not None:
                self.total = max(int(total), 1)
            if step is not None:
                self._step_text = step
        self._emit("progress")

    def warn(self, message: str) -> None:
        with self._lock:
            self.warnings.append(message)
        self._emit("warn", message=message)

    def note(self, message: str) -> None:
        with self._lock:
            self.notes.append(message)
        self._emit("note", message=message)

    def finish(self, stats: Optional[dict] = None) -> dict:
        with self._lock:
            if stats:
                self.stats.update(stats)
            ev = self._snapshot_locked()
            ev["type"] = "done"
            ev["overall"] = 100.0
            ev["pct"] = 100.0
            ev["stats"] = dict(self.stats)
            ev["warnings"] = list(self.warnings)
            ev["notes"] = list(self.notes)
            self._finished = True
        if self.sink:
            try:
                self.sink(ev)
            except Exception:  # noqa: BLE001
                pass
        return ev

    def as_json_lines(self) -> str:
        """One JSON snapshot per line — the machine-readable stream."""
        return "\n".join(json.dumps(e) for e in self.history)


class _NullReporter:
    """No-op reporter used when the caller does not ask for progress."""

    # Mirrors Reporter attributes callers may inspect (e.g. phase_id).
    phase_id: Optional[str] = None

    def phase(self, *a, **k):
        pass

    def step(self, *a, **k):
        pass

    def tick(self, *a, **k):
        pass

    def warn(self, *a, **k):
        pass

    def note(self, *a, **k):
        pass

    def finish(self, *a, **k):
        return {}

    def snapshot(self):
        return {}


NULL = _NullReporter()
