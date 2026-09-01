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
      "warnings": [...],          # accumulated (bounded, deduplicated)
      "notes": [...],             # accumulated (bounded, deduplicated)
      "warnings_total": 512,      # incl. duplicates / beyond the display cap
    }
"""
from __future__ import annotations

import json
import threading
import time
from typing import Callable, Dict, List, Optional

# Each phase contributes this fraction of the overall bar (sums to 1.0).
# Order must match execution order so the bar only ever moves forward.  The
# chunk loop decodes the image/font banks *while* it walks the chunks, so
# "images" sits between "chunks" and "frames": with the old order the bar
# jumped back by tens of percent halfway through and looked stuck.
_PHASE_WEIGHTS: Dict[str, float] = {
    "read": 0.03,
    "detect": 0.03,
    "pack": 0.04,
    "gamedata": 0.06,
    "chunks": 0.16,
    "objects": 0.04,
    "images": 0.26,
    "sounds": 0.02,
    "frames": 0.10,
    "events": 0.04,
    "build": 0.06,
    "transpile": 0.12,
    "zip": 0.04,
}

PHASE_ORDER = list(_PHASE_WEIGHTS.keys())

#: Progress events below this count are never rate limited, so short
#: conversions (and unit tests) still see one event per step.
_BURST_FREE_EVENTS = 64


def _default_phase_title(phase: str) -> str:
    return {
        "detect": "Detecting input format",
        "read": "Reading input file",
        "pack": "Reading EXE pack",
        "gamedata": "Reading game data",
        "chunks": "Decrypting game chunks",
        "objects": "Parsing objects",
        "images": "Decoding images to PNG",
        "sounds": "Indexing sound bank",
        "frames": "Parsing frames",
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

    Two rules keep a *long* conversion responsive instead of flooding it —
    a flooded stream is what made the desktop app look frozen on big games,
    because every snapshot was pushed through a pipe and re-rendered:

    * routine ``progress`` events are rate limited to ``min_interval``
      apart, and the newest throttled snapshot is always flushed before the
      next phase/warning/finish, so a phase's final numbers are never lost;
      ``phase``/``warn``/``note``/``done`` events are never delayed.
    * warnings and notes are deduplicated and bounded, and their lists ride
      along only in snapshots that changed them (or that ask for the full
      state), instead of being copied and re-encoded thousands of times.
    """

    #: how many distinct warnings/notes are kept for the UI panels
    max_listed = 150

    def __init__(self, title: str = "Converting", sink: Optional[Callable[[dict], None]] = None,
                 max_history: int = 400, min_interval: float = 0.04):
        self.title = title
        self.sink = sink
        self.min_interval = max(float(min_interval), 0.0)
        self._lock = threading.Lock()
        self._t0 = time.monotonic()
        self.phase_id: Optional[str] = None
        self.phase_title: str = ""
        self._step_text: str = ""
        self.done: int = 0
        self.total: int = 1
        self.warnings: List[str] = []
        self.notes: List[str] = []
        self.suppressed_warnings: int = 0
        self.suppressed_notes: int = 0
        self._seen_warnings: set = set()
        self._seen_notes: set = set()
        self._lists_dirty = True      # warnings/notes changed since last shown
        self.history: List[dict] = []  # bounded event replay for SSE
        self._max_history = max_history
        self._finished = False
        self.stats: dict = {}
        self._emitted = 0
        self._last_emit = 0.0
        self._pending: Optional[dict] = None   # throttled snapshot awaiting a flush

    # -- internals ----------------------------------------------------------

    def snapshot(self) -> dict:
        """Current full state (used by pollers, so it always carries lists)."""
        with self._lock:
            return self._snapshot_locked(full=True)

    def _snapshot_locked(self, full: bool = False) -> dict:
        # Clamp: a phase tick can momentarily run ahead of its total when an
        # image-bank sub-phase swaps in a smaller total; the bar must never
        # show e.g. 4200%.
        pct = (self.done / self.total * 100.0) if self.total > 0 else 0.0
        pct = min(max(pct, 0.0), 100.0)
        overall = 0.0
        if self.phase_id:
            order = PHASE_ORDER
            if self.phase_id in order:
                idx = order.index(self.phase_id)
                overall = sum(_PHASE_WEIGHTS[p] for p in order[:idx])
                overall += _PHASE_WEIGHTS[self.phase_id] * (pct / 100.0)
        show = full or self._lists_dirty
        if show:
            self._lists_dirty = False
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
            "warnings_total": len(self.warnings) + self.suppressed_warnings,
            "notes_total": len(self.notes) + self.suppressed_notes,
            "warnings": list(self.warnings) if show else [],
            "notes": list(self.notes) if show else [],
        }

    def _record_locked(self, ev: dict) -> None:
        self.history.append(ev)
        if len(self.history) > self._max_history:
            del self.history[: len(self.history) - self._max_history]

    def _take_pending_locked(self) -> List[dict]:
        """Hand out the newest throttled snapshot, recording it now."""
        pending, self._pending = self._pending, None
        if pending is None:
            return []
        self._record_locked(pending)
        return [pending]

    def _emit(self, etype: str, **extra) -> dict:
        flush: List[dict] = []
        with self._lock:
            now = time.monotonic()
            if (etype == "progress" and self.min_interval > 0
                    and self._emitted > _BURST_FREE_EVENTS
                    and now - self._last_emit < self.min_interval):
                # Throttled: keep the state, emit it as soon as the rate
                # limit opens up again (or when the phase ends).
                ev = self._snapshot_locked(full=True)
                ev.update(extra)
                self._pending = ev
                return ev
            flush = self._take_pending_locked()
            ev = self._snapshot_locked()
            ev["type"] = etype
            ev.update(extra)
            self._record_locked(ev)
            self._emitted += len(flush) + 1
            self._last_emit = now
        self._push([*flush, ev])
        return ev

    def _push(self, events: List[dict]) -> None:
        sink = self.sink
        if not sink:
            return
        for ev in events:
            try:
                sink(ev)
            except Exception:  # noqa: BLE001 - a broken sink must not kill conversion
                pass

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
            self._lists_dirty = True
            if message in self._seen_warnings:
                self.suppressed_warnings += 1
                return
            self._seen_warnings.add(message)
            if len(self.warnings) >= self.max_listed:
                self.suppressed_warnings += 1
                return
            self.warnings.append(message)
        self._emit("warn", message=message)

    def note(self, message: str) -> None:
        with self._lock:
            self._lists_dirty = True
            if message in self._seen_notes:
                self.suppressed_notes += 1
                return
            self._seen_notes.add(message)
            if len(self.notes) >= self.max_listed:
                self.suppressed_notes += 1
                return
            self.notes.append(message)
        self._emit("note", message=message)

    def finish(self, stats: Optional[dict] = None) -> dict:
        with self._lock:
            if stats:
                self.stats.update(stats)
            flush = self._take_pending_locked()
            ev = self._snapshot_locked(full=True)
            ev["type"] = "done"
            ev["overall"] = 100.0
            ev["pct"] = 100.0
            ev["stats"] = dict(self.stats)
            ev["warnings"] = list(self.warnings)
            ev["notes"] = list(self.notes)
            ev["warnings_total"] = len(self.warnings) + self.suppressed_warnings
            self._finished = True
            self._record_locked(ev)
        self._push([*flush, ev])
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
