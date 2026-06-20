# Anti-Idle GUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a PySide6 desktop GUI that prevents Windows from going idle/locking by nudging the cursor 5px (Up→Right→Down→Left cycle) after 10 minutes of no movement, with Start/Stop buttons and a log view.

**Architecture:** Three layers with clean seams — a pure `IdleNudgeController` state machine (when/which-direction to nudge, fully unit-tested), a thin `win32_input` ctypes layer (read cursor via `GetCursorPos`, nudge via `SendInput`, keep-awake via `SetThreadExecutionState`), and a `MainWindow` GUI that drives a 60 s `QTimer` through the controller into the Win32 layer and logs results to the GUI + a per-run file.

**Tech Stack:** Python 3.12+, `uv`, PySide6 6.10.3, `ctypes` (stdlib) for Win32, `pytest` (dev) for tests.

## Global Constraints

(Every task implicitly includes these — copied verbatim from the spec / project rules.)

- Platform: **Windows 10 / 11 only**. Python **3.12+**. Package manager **`uv`**.
- Run Python only via **`uv run`** — never bare `python`.
- **No new runtime dependency** for mouse handling: use Win32 via `ctypes`. Runtime deps stay `pyside6==6.10.3` + `nuitka`. `pytest` is a **dev** dependency only.
- Constants (single source of truth in `controller.py`): `DETECT_INTERVAL_SEC = 60`, `IDLE_THRESHOLD = 10`, `STEP_PIXELS = 5`.
- Direction cycle order `UP → RIGHT → DOWN → LEFT`; deltas sum to `(0, 0)` (no net drift). Screen coords: +x right, +y down.
- All code, comments, docstrings, UI strings, commit messages in **English**.
- Line length up to **120 chars**. Every function/class has a docstring. Comment non-obvious logic.
- Log line format: `YYYY-MM-DD HH:MM:SS  <message>`. Log file: `logs/antiidle_YYYYMMDD-HHMMSS.log` (per run, append mode).
- Fail loud: Win32 failures and log-file errors produce a `WARN:` line, never a silent skip or crash.

---

### Task 1: Pure idle controller + unit tests

**Files:**
- Create: `controller.py`
- Create: `tests/test_controller.py`
- Modify: `pyproject.toml` (add pytest dev dep + pytest config)

**Interfaces:**
- Consumes: nothing (pure stdlib).
- Produces (later tasks rely on these exact names/types):
  - Module constants `DETECT_INTERVAL_SEC: int`, `IDLE_THRESHOLD: int`, `STEP_PIXELS: int`.
  - `class Direction(Enum)` with members `UP, RIGHT, DOWN, LEFT`; each exposes `.dx: int`, `.dy: int`, and `.name: str` (Enum built-in).
  - `class IdleNudgeController` with `reset() -> None`, `on_tick(current_position: tuple[int, int]) -> Direction | None`, `sync_position(position: tuple[int, int]) -> None`.

- [ ] **Step 1: Add pytest as a dev dependency and configure it**

Run:
```bash
uv add --dev pytest
```

Then append to `pyproject.toml` (so the repo root is importable and `uv run pytest` finds the tests):
```toml
[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_controller.py`:
```python
"""Unit tests for the pure IdleNudgeController state machine."""
from controller import Direction, IdleNudgeController, IDLE_THRESHOLD


def _tick_n(controller, position, n):
    """Call on_tick n times with the same position; return the list of results."""
    return [controller.on_tick(position) for _ in range(n)]


def test_first_sample_is_baseline_and_never_nudges():
    # Why: the first read only establishes "where the cursor started"; it can't
    # mean "idle" yet because there is nothing to compare against.
    c = IdleNudgeController()
    assert c.on_tick((100, 100)) is None


def test_nudges_only_after_threshold_identical_samples():
    # Why: the core idle policy — the cursor must sit still for IDLE_THRESHOLD
    # samples before we touch it, never sooner.
    c = IdleNudgeController()
    results = _tick_n(c, (100, 100), IDLE_THRESHOLD)
    assert results[:-1] == [None] * (IDLE_THRESHOLD - 1)
    assert results[-1] == Direction.UP


def test_movement_resets_the_idle_counter():
    # Why: real user activity must defer nudging — otherwise we'd fight the user.
    c = IdleNudgeController()
    _tick_n(c, (100, 100), 4)              # 4 identical samples (count reaches 4)
    assert c.on_tick((200, 200)) is None   # user moved → counter resets to 1
    # 8 more identical samples bring the count to 9 — still below threshold.
    results = _tick_n(c, (200, 200), IDLE_THRESHOLD - 2)
    assert all(r is None for r in results)
    # The next sample (10th since the move) finally nudges.
    assert c.on_tick((200, 200)) == Direction.UP


def test_directions_cycle_up_right_down_left_and_wrap():
    # Why: no net drift — the cursor must not march off in one direction.
    c = IdleNudgeController()
    nudges = [r for r in _tick_n(c, (100, 100), IDLE_THRESHOLD * 5) if r is not None]
    assert nudges == [Direction.UP, Direction.RIGHT, Direction.DOWN, Direction.LEFT, Direction.UP]


def test_full_cycle_deltas_sum_to_zero():
    # Why: encodes "eventually returns to origin" as an arithmetic invariant.
    cycle = [Direction.UP, Direction.RIGHT, Direction.DOWN, Direction.LEFT]
    assert sum(d.dx for d in cycle) == 0
    assert sum(d.dy for d in cycle) == 0


def test_cadence_resets_after_nudge_and_sync():
    # Why: after a nudge, the next one must wait another full IDLE_THRESHOLD
    # (~10 idle minutes) — it must NOT fire every minute.
    c = IdleNudgeController()
    _tick_n(c, (100, 100), IDLE_THRESHOLD)    # first nudge fires here (UP)
    c.sync_position((100, 95))                # GUI moved the cursor up 5px
    results = _tick_n(c, (100, 95), IDLE_THRESHOLD - 1)
    assert all(r is None for r in results)    # 9 samples → still no nudge
    assert c.on_tick((100, 95)) == Direction.RIGHT  # 10th → next nudge, next direction
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_controller.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'controller'` (collection error).

- [ ] **Step 4: Implement `controller.py`**

Create `controller.py`:
```python
"""Pure idle-detection state machine for the anti-idle GUI.

No Qt, no Win32, no time, no I/O — fed one cursor position per sample by the GUI.
This isolation is what makes the nudge policy unit-testable without moving a real mouse.
"""
from __future__ import annotations

from enum import Enum

# --- Tunable constants (single source of truth) ---
DETECT_INTERVAL_SEC = 60   # seconds between cursor samples
IDLE_THRESHOLD = 10        # consecutive identical samples before a nudge (the 10th triggers it)
STEP_PIXELS = 5            # nudge distance per move, in pixels


class Direction(Enum):
    """One 5px nudge direction carrying its (dx, dy) screen-coordinate delta."""

    UP = (0, -STEP_PIXELS)
    RIGHT = (STEP_PIXELS, 0)
    DOWN = (0, STEP_PIXELS)
    LEFT = (-STEP_PIXELS, 0)

    @property
    def dx(self) -> int:
        """Horizontal delta in pixels (positive = right)."""
        return self.value[0]

    @property
    def dy(self) -> int:
        """Vertical delta in pixels (positive = down)."""
        return self.value[1]


# Cycle order — the four deltas sum to (0, 0), so a full cycle leaves no net drift.
_CYCLE = [Direction.UP, Direction.RIGHT, Direction.DOWN, Direction.LEFT]


class IdleNudgeController:
    """Decides when to nudge the cursor and in which direction, from a stream of samples.

    Feed it the current cursor position once per sample via on_tick(); it returns a
    Direction when the cursor has been idle for IDLE_THRESHOLD consecutive samples
    (then resets its counter and advances the direction cycle), otherwise None.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """Clear all state. Called when monitoring (re)starts."""
        self._last_position: tuple[int, int] | None = None
        self._idle_count: int = 0
        self._direction_index: int = 0

    def on_tick(self, current_position: tuple[int, int]) -> Direction | None:
        """Process one cursor sample.

        Args:
            current_position: the (x, y) cursor position read this sample.

        Returns:
            The Direction to nudge when the idle threshold is reached (counter resets,
            cycle advances), otherwise None.
        """
        if self._last_position is None:
            # First sample only establishes the baseline; it can never be a nudge.
            self._last_position = current_position
            self._idle_count = 1
            return None

        if current_position == self._last_position:
            self._idle_count += 1
        else:
            # Activity (user moved the cursor) defers nudging: restart the streak.
            self._idle_count = 1
            self._last_position = current_position
            return None

        if self._idle_count >= IDLE_THRESHOLD:
            direction = _CYCLE[self._direction_index]
            self._direction_index = (self._direction_index + 1) % len(_CYCLE)
            self._idle_count = 0
            return direction

        return None

    def sync_position(self, position: tuple[int, int]) -> None:
        """Update the baseline to `position` without touching the idle counter.

        Called by the GUI after it performs a nudge, passing the actual post-move
        cursor position. This makes the next nudge require a fresh IDLE_THRESHOLD of
        idle samples instead of firing again on the next tick.
        """
        self._last_position = position
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_controller.py -v`
Expected: PASS — 6 passed.

- [ ] **Step 6: Commit**

```bash
git add controller.py tests/test_controller.py pyproject.toml uv.lock
git commit -m "$(cat <<'EOF'
feat: add pure idle-nudge controller with unit tests

IdleNudgeController decides when (after IDLE_THRESHOLD idle samples) and
which direction (U->R->D->L cycle, no net drift) to nudge. Pure stdlib,
fully unit-tested without a real mouse. Adds pytest as a dev dependency.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Win32 input layer (ctypes)

**Files:**
- Create: `win32_input.py`
- Create: `tests/test_win32_input.py`

**Interfaces:**
- Consumes: nothing (stdlib `ctypes` only).
- Produces (the GUI relies on these exact signatures):
  - `get_cursor_pos() -> tuple[int, int]` (raises `OSError` on failure).
  - `move_mouse_relative(dx: int, dy: int) -> bool`.
  - `set_keep_awake() -> bool`.
  - `clear_keep_awake() -> bool`.

- [ ] **Step 1: Write the failing (non-intrusive) smoke tests**

Create `tests/test_win32_input.py`:
```python
"""Non-intrusive smoke tests for the Win32 ctypes layer.

These verify the ctypes plumbing (struct sizes, argtypes) actually works on
Windows without disturbing the user: the only move performed is 0px.
"""
import sys

import pytest

import win32_input

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Win32 API only available on Windows")


def test_get_cursor_pos_returns_int_pair():
    pos = win32_input.get_cursor_pos()
    assert isinstance(pos, tuple) and len(pos) == 2
    assert all(isinstance(v, int) for v in pos)


def test_zero_move_is_harmless_noop_and_succeeds():
    # Why: a 0px relative move exercises the full SendInput struct/argtypes path
    # (the riskiest plumbing) without moving the user's cursor.
    assert win32_input.move_mouse_relative(0, 0) is True


def test_keep_awake_roundtrip_succeeds():
    # Why: confirms SetThreadExecutionState is wired correctly in both directions.
    assert win32_input.set_keep_awake() is True
    assert win32_input.clear_keep_awake() is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_win32_input.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'win32_input'` (collection error).

- [ ] **Step 3: Implement `win32_input.py`**

Create `win32_input.py`:
```python
"""Thin Win32 (ctypes) wrappers: read the cursor, nudge it, and keep Windows awake.

Windows-only. Each call sets argtypes/restype explicitly to avoid 64-bit pointer
truncation, and returns a success boolean (or the position) so callers can fail loud.

Why SendInput rather than SetCursorPos for the nudge: a synthesized relative
MOUSEEVENTF_MOVE reliably resets the system idle timer (so the lock screen is
actually prevented), whereas SetCursorPos merely teleports the cursor and often
does not reset it.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

# --- Win32 constants ---
INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002

# Pointer-sized unsigned integer for the ULONG_PTR field (8 bytes on 64-bit).
ULONG_PTR = ctypes.c_size_t

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class MOUSEINPUT(ctypes.Structure):
    """Win32 MOUSEINPUT; field order must match the C definition exactly."""

    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _INPUTUNION(ctypes.Union):
    """The DUMMYUNIONNAME union inside INPUT. MOUSEINPUT is the largest member,
    so sizing the union to it matches the real INPUT layout."""

    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    """Win32 INPUT structure (mouse variant)."""

    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", _INPUTUNION),
    ]


# GetCursorPos(LPPOINT) -> BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype = wintypes.BOOL

# SendInput(UINT nInputs, LPINPUT pInputs, int cbSize) -> UINT
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT

# SetThreadExecutionState(EXECUTION_STATE) -> EXECUTION_STATE
kernel32.SetThreadExecutionState.argtypes = [wintypes.DWORD]
kernel32.SetThreadExecutionState.restype = wintypes.DWORD


def get_cursor_pos() -> tuple[int, int]:
    """Return the current cursor position as (x, y).

    Raises:
        OSError: if GetCursorPos fails (raised via ctypes.WinError).
    """
    point = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        raise ctypes.WinError(ctypes.get_last_error())
    return (point.x, point.y)


def move_mouse_relative(dx: int, dy: int) -> bool:
    """Move the cursor by (dx, dy) pixels using synthesized input (SendInput).

    Returns:
        True iff exactly one input event was inserted.
    """
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp.union.mi = MOUSEINPUT(dx=dx, dy=dy, mouseData=0, dwFlags=MOUSEEVENTF_MOVE, time=0, dwExtraInfo=0)
    sent = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    return sent == 1


def set_keep_awake() -> bool:
    """Tell Windows the system and display are in use (prevents idle sleep/lock).

    Returns:
        True iff the call succeeds (non-zero return).
    """
    result = kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
    return result != 0


def clear_keep_awake() -> bool:
    """Release the keep-awake request set by set_keep_awake().

    Returns:
        True iff the call succeeds (non-zero return).
    """
    result = kernel32.SetThreadExecutionState(ES_CONTINUOUS)
    return result != 0
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_win32_input.py -v`
Expected: PASS — 3 passed (on Windows). On non-Windows: 3 skipped.

- [ ] **Step 5: Commit**

```bash
git add win32_input.py tests/test_win32_input.py
git commit -m "$(cat <<'EOF'
feat: add Win32 ctypes input layer

GetCursorPos to read, SendInput relative move to nudge (resets the idle
timer, unlike SetCursorPos), SetThreadExecutionState to keep Windows
awake. Non-intrusive smoke tests verify the ctypes plumbing.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: GUI window + entry point

**Files:**
- Create: `gui.py`
- Modify: `antiidle.py` (replace the stub with the real entry point)

**Interfaces:**
- Consumes: `controller.DETECT_INTERVAL_SEC`, `controller.IDLE_THRESHOLD`, `controller.IdleNudgeController`; `win32_input.get_cursor_pos/move_mouse_relative/set_keep_awake/clear_keep_awake`.
- Produces: `gui.MainWindow` (QWidget); `antiidle.main() -> None`.

- [ ] **Step 1: Implement `gui.py`**

Create `gui.py`:
```python
"""PySide6 main window: Start/Stop buttons, log view, the 60s sampling timer, and file logging."""
from __future__ import annotations

import os
from datetime import datetime

from PySide6.QtCore import QTimer, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QHBoxLayout, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

import win32_input
from controller import DETECT_INTERVAL_SEC, IDLE_THRESHOLD, IdleNudgeController


class MainWindow(QWidget):
    """Top-level window wiring the sampling timer to the idle controller and the Win32 layer."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Anti-Idle")
        self.resize(480, 360)

        self._controller = IdleNudgeController()
        self._timer = QTimer(self)
        self._timer.setInterval(DETECT_INTERVAL_SEC * 1000)
        self._timer.timeout.connect(self._on_tick)

        self._log_file = self._open_log_file()  # file handle, or None if it couldn't be opened

        self._start_button = QPushButton("Start")
        self._stop_button = QPushButton("Stop")
        self._stop_button.setEnabled(False)  # nothing to stop until Start is pressed
        self._start_button.clicked.connect(self._on_start)
        self._stop_button.clicked.connect(self._on_stop)

        self._log_view = QPlainTextEdit()
        self._log_view.setReadOnly(True)

        button_row = QHBoxLayout()
        button_row.addWidget(self._start_button)
        button_row.addWidget(self._stop_button)

        layout = QVBoxLayout(self)
        layout.addLayout(button_row)
        layout.addWidget(self._log_view)

    def _open_log_file(self):
        """Open a per-run append-mode log file under logs/. Returns the handle, or None on failure."""
        try:
            os.makedirs("logs", exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            return open(os.path.join("logs", f"antiidle_{stamp}.log"), "a", encoding="utf-8")
        except OSError:
            return None

    def log(self, message: str) -> None:
        """Append a timestamped line to the GUI log view and the log file (if open)."""
        line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {message}"
        self._log_view.appendPlainText(line)
        if self._log_file is not None:
            try:
                self._log_file.write(line + "\n")
                self._log_file.flush()
            except OSError:
                # Fail loud but keep running with GUI-only logging.
                warn = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  WARN: failed to write log file."
                self._log_view.appendPlainText(warn)
                self._log_file = None

    @Slot()
    def _on_start(self) -> None:
        """Start monitoring: reset the controller, request keep-awake, start the timer."""
        self._controller.reset()
        if not win32_input.set_keep_awake():
            self.log("WARN: SetThreadExecutionState failed; relying on mouse nudge only.")
        self._timer.start()
        self._start_button.setEnabled(False)
        self._stop_button.setEnabled(True)
        self.log("Started monitoring.")

    @Slot()
    def _on_stop(self) -> None:
        """Stop monitoring: stop the timer and release keep-awake."""
        self._timer.stop()
        win32_input.clear_keep_awake()
        self._start_button.setEnabled(True)
        self._stop_button.setEnabled(False)
        self.log("Stopped monitoring.")

    @Slot()
    def _on_tick(self) -> None:
        """One sampling cycle: read cursor, ask controller, nudge + log if the idle threshold is hit."""
        try:
            position = win32_input.get_cursor_pos()
        except OSError:
            self.log("WARN: GetCursorPos failed; skipping this sample.")
            return

        direction = self._controller.on_tick(position)
        if direction is None:
            return

        if not win32_input.move_mouse_relative(direction.dx, direction.dy):
            self.log("WARN: SendInput failed; mouse not moved.")
            return

        # Feed the controller the real post-move position so the next nudge waits a full cycle.
        try:
            self._controller.sync_position(win32_input.get_cursor_pos())
        except OSError:
            self.log("WARN: GetCursorPos failed after nudge.")
        self.log(f"Idle {IDLE_THRESHOLD} reads detected → moved mouse {direction.name} (5px).")

    def closeEvent(self, event: QCloseEvent) -> None:
        """Release keep-awake and close the log file when the window closes."""
        self._timer.stop()
        win32_input.clear_keep_awake()
        if self._log_file is not None:
            self._log_file.close()
        event.accept()
```

- [ ] **Step 2: Replace the stub in `antiidle.py`**

Replace the entire contents of `antiidle.py` with:
```python
"""Entry point for the Anti-Idle GUI application."""
import sys

from PySide6.QtWidgets import QApplication

from gui import MainWindow


def main() -> None:
    """Launch the Anti-Idle GUI."""
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Verify the full test suite still passes**

Run: `uv run pytest -v`
Expected: PASS — 9 passed (6 controller + 3 win32) on Windows.

- [ ] **Step 4: Manual smoke test of the GUI**

Run: `uv run python antiidle.py`
Verify:
1. A window titled "Anti-Idle" opens with **Start** enabled, **Stop** disabled, and an empty log area.
2. Click **Start** → log shows `... Started monitoring.`; Start becomes disabled, Stop enabled.
3. Click **Stop** → log shows `... Stopped monitoring.`; Start enabled, Stop disabled.
4. A file `logs/antiidle_<timestamp>.log` exists and contains the same lines.
5. Close the window → process exits cleanly (no traceback).

To observe an actual nudge quickly without waiting 10 minutes, **temporarily** set `DETECT_INTERVAL_SEC = 1` and `IDLE_THRESHOLD = 3` in `controller.py`, run again, click Start, and don't touch the mouse: within ~3 s the cursor jumps 5px and the log shows `Idle 3 reads detected → moved mouse UP (5px)`, then RIGHT, DOWN, LEFT on subsequent triggers. **Revert both constants to 60 and 10 before committing.**

- [ ] **Step 5: Commit**

```bash
git add gui.py antiidle.py
git commit -m "$(cat <<'EOF'
feat: add PySide6 GUI and entry point

MainWindow wires a 60s QTimer through IdleNudgeController into the Win32
layer: Start requests keep-awake and begins sampling, Stop releases it,
nudges and Start/Stop are logged to the view and a per-run logs/ file.
closeEvent releases keep-awake and closes the log file.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: README with usage instructions

**Files:**
- Modify: `README.md` (currently empty; `CLAUDE.md` points to it for architecture/usage).

**Interfaces:** none (documentation only).

- [ ] **Step 1: Write `README.md`**

Replace the contents of `README.md` with:
```markdown
# antiidle

A small Windows GUI that prevents the screen from locking / the system from going idle.

While running, it samples the cursor position once a minute. After 10 consecutive
identical samples (~10 minutes of no movement) it nudges the cursor 5px, cycling
through Up → Right → Down → Left so there is no net drift over a full cycle. It also
sets `SetThreadExecutionState` while active as a safety net. Each nudge and each
Start/Stop is logged to the window and to a per-run file under `logs/`.

## Requirements

- Windows 10 / 11
- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/)

## Install

```
uv sync
```

## Run

```
uv run python antiidle.py
```

Click **Start** to begin, **Stop** to halt. Logs appear in the window and in
`logs/antiidle_<timestamp>.log`.

## Test

```
uv run pytest
```

## Configuration

Edit the constants at the top of `controller.py`:

- `DETECT_INTERVAL_SEC` (default 60) — seconds between cursor samples
- `IDLE_THRESHOLD` (default 10) — consecutive identical samples before a nudge
- `STEP_PIXELS` (default 5) — nudge distance in pixels

## Architecture

- `controller.py` — pure idle-detection state machine (`IdleNudgeController`), no Qt/Win32; unit-tested.
- `win32_input.py` — thin `ctypes` wrappers: `GetCursorPos`, `SendInput`, `SetThreadExecutionState`.
- `gui.py` — `MainWindow`: buttons, log view, the sampling `QTimer`, file logging.
- `antiidle.py` — entry point.

## Build a standalone executable

```
uv run python -m nuitka --standalone --enable-plugin=pyside6 --windows-console-mode=disable antiidle.py
```
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
docs: add README with install/run/test and architecture

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Notes / known edge cases (from the spec)

- **Implementation correction (2026-06-20): `move_mouse_relative` uses an ABSOLUTE `SendInput`, not the relative move shown in Task 2's code block.** Physical testing proved relative `MOUSEEVENTF_MOVE` deltas are pointer-acceleration "mickeys", not pixels — a 5px request moved **0px**. The shipped `win32_input.py` instead computes `target = current + delta`, converts to `0..65535` normalized virtual-desktop coords, and sends `MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK` (guarded against GetSystemMetrics failure, clamped to `[0,65535]`). Same `move_mouse_relative(dx, dy) -> bool` contract, so the controller/GUI are unchanged. See spec §5 and `win32_input.py`. The Task 2 relative code below is retained for historical accuracy only.
- At the extreme screen edge the absolute target is clamped, so a single 4-step cycle may not return *exactly* to the origin (also ±1px from `65535`-grid rounding). Rare and harmless; `sync_position` keeps idle detection correct regardless.
- The first sample on each Start only establishes the baseline (never nudges). The first cursor read happens on the first timer tick (after 60 s); no immediate sample is taken on Start.
- No system tray, pause/resume, settings UI, or auto-start (YAGNI). Closing the window exits the app.
- Spec §8 listed `closeEvent + aboutToQuit` for keep-awake cleanup; this plan uses `closeEvent` only. For a single-window app with no tray/menu, closing the window is the only exit path, so `closeEvent` fully covers it, and `clear_keep_awake()` is idempotent/harmless if called more than once. This avoids depending on the unverified `aboutToQuit` signal.
