# Anti-Idle GUI — Design Spec

- **Date:** 2026-06-20
- **Status:** Approved (brainstorming complete; pending implementation plan)
- **Platform:** Windows 10 / 11 only · Python 3.12+ · `uv` · PySide6 6.10.3

## 1. Goal

A small PySide6 desktop GUI that prevents Windows from going idle / locking. It has **Start** and **Stop**
buttons and a **log area**. While running, it samples the cursor position once per minute; after 10 consecutive
identical samples (≈10 minutes of no movement) it nudges the cursor 5 px, cycling through the directions
Up → Right → Down → Left so there is no net drift over a full cycle. Each nudge and each Start/Stop is logged
to the GUI and to a per-run log file.

## 2. Confirmed decisions

| # | Decision | Choice |
|---|----------|--------|
| 1 | Mouse read + move library | **Win32 via `ctypes`** — `GetCursorPos` to read, `SendInput` (relative move) to nudge. No new dependency; `SendInput` reliably resets the system idle timer. |
| 2 | Per-trigger behavior | **One 5 px move per trigger**, advancing the U→R→D→L cycle (returns to origin over 4 separate triggers). |
| 3 | Safety net | **Yes** — call `SetThreadExecutionState` while running, on top of the mouse nudge. |
| 4 | Log scope | **Nudges + Start/Stop** (not every 60 s read). |
| 5 | Log persistence | **GUI + per-run log file** on disk. |

## 3. Architecture

Four focused modules, separating pure logic (unit-testable) from Qt and Win32 (not).

| File | Responsibility | Depends on |
|------|----------------|------------|
| `controller.py` | Pure logic: `Direction` enum + `IdleNudgeController`. Decides *when* to nudge and *which direction*. No Qt, no Win32, no I/O. | stdlib only |
| `win32_input.py` | Thin `ctypes` wrappers: `get_cursor_pos()`, `move_mouse_relative(dx, dy)`, `set_keep_awake()`, `clear_keep_awake()`. | `ctypes` |
| `gui.py` | `MainWindow`: Start/Stop buttons, log view, `QTimer`, log-file writer. Wires timer → controller → win32 + log. | PySide6, `controller`, `win32_input` |
| `antiidle.py` | Entry point `main()` → `QApplication` → `MainWindow`. Replaces the current stub. | `gui` |
| `tests/test_controller.py` | Unit tests for `IdleNudgeController` only. | pytest, `controller` |

## 4. Core logic — `controller.py`

### Constants (single source of truth, easy to edit)

```python
DETECT_INTERVAL_SEC = 60   # seconds between cursor samples
IDLE_THRESHOLD = 10        # consecutive identical samples before a nudge
STEP_PIXELS = 5            # nudge distance per move
```

`IDLE_THRESHOLD = 10` means "the 10th consecutive identical sample triggers the nudge". (The original
phrasing "超過10次" could be read as 11; 10 was confirmed.)

### `Direction`

An enum of the four moves, each carrying its `(dx, dy)` delta. Screen coordinates: +x = right, +y = down.

```
UP    = ( 0, -5)
RIGHT = (+5,  0)
DOWN  = ( 0, +5)
LEFT  = (-5,  0)
```

The cycle order is `[UP, RIGHT, DOWN, LEFT]`; the four deltas sum to `(0, 0)` → no net drift over a full cycle.
Deltas are derived from `STEP_PIXELS` so changing the constant changes all four.

### `IdleNudgeController`

Pure state machine. No knowledge of Qt, Win32, time, or the screen.

State:
- `last_position: tuple[int, int] | None`
- `idle_count: int`
- `direction_index: int` (0..3)

Methods:
- `reset()` — clear all state (called on Start). `idle_count = 0`, `last_position = None`, `direction_index = 0`.
- `on_tick(current_position) -> Direction | None` — called once per sample:
  1. If `last_position is None` → first sample: store it, `idle_count = 1`, return `None`.
  2. Else if `current_position == last_position` → `idle_count += 1`.
  3. Else (moved) → `idle_count = 1`, `last_position = current_position`, return `None`.
  4. If `idle_count >= IDLE_THRESHOLD`: choose `direction = cycle[direction_index]`,
     advance `direction_index = (direction_index + 1) % 4`, set `idle_count = 0`, return `direction`.
  5. Otherwise return `None`.
- `sync_position(position)` — set `last_position = position` without changing the counter. Called by the GUI
  after it performs a nudge, passing the *actual post-move cursor position*. This guarantees the next nudge
  requires another full `IDLE_THRESHOLD` of idle samples (cadence ≈ one nudge per 10 idle minutes).

**Why `sync_position` is separate from `on_tick`:** the GUI owns the side effect (the real cursor move) and the
real post-move coordinate (which may be clamped at a screen edge). The controller stays pure and is fed the
ground-truth position, so its idle detection never mistakes our own nudge for user activity nor for continued idleness.

## 5. Win32 layer — `win32_input.py`

Thin `ctypes` wrappers over `user32`/`kernel32`. Each sets `argtypes`/`restype` explicitly to avoid 64-bit
pointer truncation (a common ctypes pitfall). Functions return success booleans; failures are surfaced, not swallowed.

- `get_cursor_pos() -> tuple[int, int]` — `GetCursorPos(LPPOINT)` with `POINT { LONG x; LONG y }`.
- `move_mouse_relative(dx, dy) -> bool` — build a `MOUSEINPUT` with `dwFlags = MOUSEEVENTF_MOVE (0x0001)` and
  relative `dx/dy`, call `SendInput(1, &input, sizeof(INPUT))`. Returns `True` iff `SendInput` returns 1.
  `dwExtraInfo` uses a pointer-sized type (`ULONG_PTR`).
- `set_keep_awake() -> bool` — `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)`.
  Returns `True` iff the call returns non-zero.
- `clear_keep_awake() -> bool` — `SetThreadExecutionState(ES_CONTINUOUS)` to release the request.

Relevant flags: `ES_CONTINUOUS = 0x80000000`, `ES_SYSTEM_REQUIRED = 0x00000001`, `ES_DISPLAY_REQUIRED = 0x00000002`.

## 6. GUI — `gui.py`

`MainWindow` (a `QWidget` / `QMainWindow`). Exact Qt API calls confirmed against PySide6 6.10.3 docs (context7)
at implementation time.

Widgets / layout:
- Top row (`QHBoxLayout`): **Start** button, **Stop** button.
- Below: a read-only `QPlainTextEdit` (append-friendly) as the log view.
- A `QTimer` with interval `DETECT_INTERVAL_SEC * 1000`, connected to a `_on_tick` slot.

Behavior:
- **Start** clicked: `controller.reset()`; `win32.set_keep_awake()`; start the timer; log `"Started monitoring."`;
  disable Start, enable Stop. The first cursor sample occurs on the first timer tick (after 60 s); no immediate
  sample is taken on Start, so there is a single sampling code path.
- `_on_tick`: `pos = win32.get_cursor_pos()`; `d = controller.on_tick(pos)`; if `d` is not None →
  `win32.move_mouse_relative(d.dx, d.dy)`, then `controller.sync_position(win32.get_cursor_pos())`, then log the nudge.
- **Stop** clicked: stop the timer; `win32.clear_keep_awake()`; log `"Stopped monitoring."`;
  enable Start, disable Stop.
- Initial state: Start enabled, Stop disabled.

Button state is the single guard against double-start / double-stop (no extra running flag needed, but a
boolean may be used internally for clarity).

## 7. Logging — `gui.py`

- Line format: `YYYY-MM-DD HH:MM:SS  <message>`.
- Examples:
  - `2026-06-20 14:00:00  Started monitoring.`
  - `2026-06-20 14:10:00  Idle 10 reads detected → moved mouse UP (5px).`
  - `2026-06-20 14:25:00  Stopped monitoring.`
- A single `log(message)` helper appends the timestamped line to the `QPlainTextEdit` **and** writes it to the
  log file.
- Log file: a `logs/` folder beside the app; one file per run named `logs/antiidle_YYYYMMDD-HHMMSS.log`
  (timestamp captured when the window is created), opened in append mode.

## 8. Error handling (fail loud)

- `move_mouse_relative` / `set_keep_awake` / `clear_keep_awake` failure (return value indicates failure) →
  log a `WARN:` line in the GUI; do **not** crash the timer/monitoring.
- Log-file write error → log one `WARN:` line to the GUI and continue with GUI-only logging.
- On window close (`closeEvent`) and `QApplication.aboutToQuit` → always call `clear_keep_awake()` so the machine
  is never left in a forced keep-awake state. Stop the timer too.
- Known edge case (documented, accepted): at the extreme screen edge Windows clamps a relative move, so a single
  4-step cycle may not return *exactly* to the origin. Rare and harmless. `sync_position` keeps idle detection
  correct regardless.

## 9. Testing — `tests/test_controller.py`

Tests target `IdleNudgeController` (pure, no mouse/Qt). Each encodes *why* the behavior matters:

1. **Idle policy:** 9 identical samples → no nudge; the 10th → a nudge. *(the core idle threshold)*
2. **Activity defers nudging:** a changed position at sample 5 resets the counter so no nudge fires at sample 10
   from the original streak. *(user activity must postpone nudging — the whole point)*
3. **No-net-drift cycle:** consecutive nudges return `UP, RIGHT, DOWN, LEFT, UP, …`. *(direction cycle)*
4. **Return to origin:** the deltas of four consecutive nudges sum to `(0, 0)`. *(eventually returns to origin)*
5. **Cadence:** after a nudge + `sync_position`, another full `IDLE_THRESHOLD` of idle samples is required before
   the next nudge. *(≈ one nudge per 10 idle minutes, not a burst)*
6. **First sample:** the very first `on_tick` establishes the baseline and never nudges.

Win32 and Qt layers are kept thin and are not unit-tested (they require a real desktop session); they are
verified by manual run.

## 10. Out of scope (YAGNI)

No system-tray icon, no pause/resume, no settings UI, no auto-start-on-login, no configurable interval/threshold
via the GUI (constants are edited in code). Closing the window exits the app.

## 11. Open items

None blocking. Constants in §4 are the knobs the user is most likely to tweak later.
