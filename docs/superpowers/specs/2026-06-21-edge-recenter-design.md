# Edge-Recenter Safety for Mouse Nudge — Design Spec

- **Date:** 2026-06-21
- **Status:** Approved (brainstorming complete; pending implementation plan)
- **Platform:** Windows 10 / 11 only · Python 3.12+ · `uv` · PySide6 6.10.3

## 1. Goal

Make the anti-idle nudge robust when the cursor is parked at (or within `STEP_PIXELS` of) a screen edge.

Today the `IdleNudgeController` cycles Up → Right → Down → Left (`_CYCLE`), and the four 5 px deltas sum to
`(0, 0)`, which is the "no net drift over a full cycle" invariant. That invariant **breaks at a screen edge**:
an outward nudge (e.g. `RIGHT (+5)` when the cursor is at the right edge) is clamped by Windows to the edge, so
it does not actually displace — while the opposing nudge later in the cycle does. The result is per-cycle drift
and, at the extreme, a nudge that produces no movement at all (so the idle timer may not reset).

Fix: **before every nudge**, if the cursor is within `STEP_PIXELS` of any edge of the monitor it is currently
on, first move it to that monitor's center, then apply the directional nudge from there.

## 2. Confirmed decisions

| # | Decision | Choice |
|---|----------|--------|
| 1 | Recenter trigger | **Before every nudge** — checked on all four steps of the cycle, so a nudge can never clamp regardless of cycle position. |
| 2 | "Near edge" threshold | Distance to **any** edge `< STEP_PIXELS` (strict; matches "距離邊緣小於 STEP_PIXELS pixel"). `STEP_PIXELS = 5`, so distance `∈ {0..4}` triggers a recenter. |
| 3 | Recenter target | Center of the **monitor the cursor is currently on** (multi-monitor: `MonitorFromPoint` + `GetMonitorInfo`), **not** the virtual-desktop center. |
| 4 | Same-tick behavior | On a near-edge nudge, **recenter and then apply the directional nudge in the same tick** (cursor ends at center ± 5 px). The recenter does not consume the tick. |
| 5 | DPI / scaling | **Rely on Qt 6's default Per-Monitor-DPI-Aware-V2.** All Win32 coordinate calls then operate in consistent physical pixels. No DPI code added; documented as a reliance. |
| 6 | Failure handling | If recenter fails (e.g. monitor lookup fails), **log `WARN` and still proceed** with the directional nudge — degrade gracefully, never worse than today. |
| 7 | Layering | Win32 monitor geometry + a small testable orchestrator live in `win32_input.py`; the controller stays pure (no Win32). |

## 3. DPI / scaling rationale

Qt 6 sets the process to **Per-Monitor DPI Aware V2 by default** on Windows
(`doc.qt.io/qtforpython-6/overviews/qtdoc-highdpi.html`). Under that mode, `GetCursorPos`, `GetMonitorInfo`,
`GetSystemMetrics(SM_*VIRTUALSCREEN)`, `SetCursorPos`, and the absolute `SendInput` all report and consume
**true physical pixels**, consistently, even across monitors with different scale factors. Therefore:

- The recenter target is computed from `GetMonitorInfo`'s physical-pixel `rcMonitor` rect — never from Qt
  logical coordinates. Mixing the two is the failure mode the "注意 windows 縮放解析度" requirement guards against.
- No explicit `SetProcessDpiAwarenessContext` call is added; Qt already establishes the awareness before
  `QApplication` is created. This is documented in `win32_input.py` so the reliance is not silent.

(Note: the test process runs without Qt, so it does not get Qt's awareness. This only affects the absolute
*scale* of coordinates, not correctness — within one process every Win32 call is self-consistent, and the tests
assert relative behavior, e.g. "returns to origin", which is scale-invariant.)

## 4. New code — `win32_input.py`

### 4.1 Win32 plumbing

- `RECT` struct: `left, top, right, bottom` (`LONG`). Windows convention: `right`/`bottom` are **exclusive**
  (one past the last pixel), so the maximum valid pixel is `right - 1` / `bottom - 1`.
- `MONITORINFO` struct: `cbSize (DWORD)`, `rcMonitor (RECT)`, `rcWork (RECT)`, `dwFlags (DWORD)`. `cbSize` must be
  set to `sizeof(MONITORINFO)` before the call.
- `user32.MonitorFromPoint(POINT, DWORD) -> HMONITOR`; flag `MONITOR_DEFAULTTONEAREST = 2`. `POINT` is passed
  **by value**.
- `user32.GetMonitorInfoW(HMONITOR, LPMONITORINFO) -> BOOL`.

### 4.2 `get_monitor_bounds(x, y) -> tuple[int, int, int, int] | None`

Return `(left, top, right, bottom)` of the monitor containing `(x, y)` in physical pixels, via
`MonitorFromPoint(MONITOR_DEFAULTTONEAREST)` + `GetMonitorInfoW`. Returns `None` if `GetMonitorInfoW` fails.

### 4.3 Pure helpers (unit-testable, no Win32 — mirror `_pixel_to_normalized`)

- `_rect_center(left, top, right, bottom) -> tuple[int, int]` → `((left + right) // 2, (top + bottom) // 2)`.
- `_is_near_edge(x, y, left, top, right, bottom, margin) -> bool` → `True` if **any** of:
  `(x - left) < margin`, `((right - 1) - x) < margin`, `(y - top) < margin`, `((bottom - 1) - y) < margin`.

### 4.4 `ensure_off_edge(margin) -> str | None`

Orchestrator (testable without Qt):
1. `get_cursor_pos()` (returns `str` error on `OSError`, consistent with `move_mouse_relative`).
2. `get_monitor_bounds(...)`; if `None`, return an error string.
3. If `_is_near_edge(...)`: compute `_rect_center(...)`, then `move_mouse_relative(center_x - x, center_y - y)`
   and return its result (`None` on success, else the error string).
4. Otherwise (not near an edge): return `None` (no-op).

Reuses `move_mouse_relative` for the actual move, so the recenter goes through the same absolute-`SendInput` +
`SetCursorPos`-snap path and likewise resets the idle timer. Adopts the same `str | None` ("success or reason")
contract as `move_mouse_relative`.

## 5. GUI change — `gui._on_tick`

Between "controller returned a `direction`" and `move_mouse_relative(direction...)`:

```python
edge_error = win32_input.ensure_off_edge(STEP_PIXELS)
if edge_error is not None:
    self.log(f"WARN: recenter failed: {edge_error}")
    # Proceed anyway: a possibly-clamped nudge is no worse than today.
```

`STEP_PIXELS` is imported from `controller` (already the single source of truth). The directional nudge then
runs exactly as it does now.

## 6. Tests

### Pure helpers (no Win32)
- `_is_near_edge`: each of the four edges, with a point just inside the margin (triggers) and just outside it
  (does not), using the exclusive-`right`/`bottom` math.
- `_rect_center`: a known rect maps to its center.

### Win32 smoke (Windows-only, non-intrusive)
- `get_monitor_bounds` at the current cursor returns a rect that contains the cursor and has `left < right`,
  `top < bottom`.
- `ensure_off_edge`: force the cursor to a monitor corner (within `STEP_PIXELS` of two edges), call it, assert
  the cursor ends `≥ STEP_PIXELS` from every edge; restore the original position afterward.
- `ensure_off_edge` from a centered position is a no-op (returns `None`, cursor effectively unmoved).

### Strengthen the existing flaky test
- `test_full_cycle_returns_to_origin_exactly`: call `ensure_off_edge(STEP_PIXELS)` first so the cursor never
  starts at an edge, then assert the U→R→D→L cycle returns to the exact (post-recenter) origin. This removes
  the environmental edge-clamp flakiness that made the test fail when the cursor was parked at the screen edge.

## 7. Out of scope

- No change to the controller's timing or direction policy.
- No change to `move_mouse_relative`'s mechanism (still absolute `SendInput` + `SetCursorPos` snap).
- No explicit DPI-awareness API call (Qt's default covers it).
- No new runtime dependency.
