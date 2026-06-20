# Edge-Recenter Safety for Mouse Nudge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Before every nudge, if the cursor is within `STEP_PIXELS` of any edge of its current monitor, move it to that monitor's center first, so an outward nudge can never be clamped at the edge (which breaks movement and the cycle's zero-net-drift invariant).

**Architecture:** Add Win32 monitor geometry plus two pure decision helpers and a small testable orchestrator (`ensure_off_edge`) to `win32_input.py`; the orchestrator reuses `move_mouse_relative` to perform the recenter. `gui._on_tick` calls `ensure_off_edge(STEP_PIXELS)` before each directional nudge. The pure `IdleNudgeController` stays untouched (no Win32, no screen geometry).

**Tech Stack:** Python 3.12, `ctypes` (Win32 `user32`), PySide6 6.10.3, pytest, `uv`.

**Spec:** `docs/superpowers/specs/2026-06-21-edge-recenter-design.md`

## Global Constraints

- Platform: Windows 10 / 11 only. Python 3.12+. Package manager `uv`. PySide6 6.10.3.
- Run everything with `uv run` — never bare `python`/`pytest`.
- No new runtime dependency.
- All code, comments, docstrings, and docs in English. Lines up to 120 chars.
- Every function/class has a docstring scaled to its complexity.
- DPI: rely on Qt 6's default Per-Monitor-DPI-Aware-V2. Monitor geometry is in **physical pixels**; never mix with Qt logical coordinates.
- Windows `RECT` `right`/`bottom` are **exclusive** (last valid pixel = `right - 1` / `bottom - 1`).
- "Near edge" = distance to any edge **strictly less than** `margin` (`STEP_PIXELS = 5`, from `controller.py`).
- Commit trailer on every commit: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

## Preliminary: baseline commit (needs user go-ahead)

The working tree already contains the reviewed, tested in-flight change that turned `move_mouse_relative` into a `str | None` contract plus the `/simplify` cleanup (`win32_input.py`, `gui.py`, `tests/test_win32_input.py`). The feature below builds directly on it and touches the same files. Per project policy ("commit only when asked"), get the user's OK, then commit that baseline first so the feature commits stay clean:

```bash
git add win32_input.py gui.py tests/test_win32_input.py
git commit -m "feat: surface detailed Win32 error from move_mouse_relative

move_mouse_relative now returns str | None (None on success, else the failed
Win32 call plus formatted GetLastError) so the GUI logs the real reason a nudge
was dropped instead of a generic 'SendInput failed'.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

If the user prefers to fold the baseline into the first feature commit instead, skip this and let Task 1's commit pick up the combined diff.

---

## Task 1: Pure geometry helpers (`_rect_center`, `_is_near_edge`)

**Files:**
- Modify: `win32_input.py` (add two pure functions after `_pixel_to_normalized`, before `move_mouse_relative`)
- Test: `tests/test_win32_input.py` (add tests; module already has `pytestmark = skipif(sys.platform != "win32")`)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `_rect_center(left: int, top: int, right: int, bottom: int) -> tuple[int, int]`
  - `_is_near_edge(x: int, y: int, left: int, top: int, right: int, bottom: int, margin: int) -> bool`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_win32_input.py`:

```python
def test_rect_center_of_monitor():
    # Why: the recenter target must be the monitor's center pixel; right/bottom are exclusive.
    assert win32_input._rect_center(0, 0, 1920, 1080) == (960, 540)
    assert win32_input._rect_center(1920, 0, 3840, 1080) == (2880, 540)  # right monitor of a dual setup


def test_is_near_edge_true_at_each_edge():
    # Why: a cursor within `margin` of ANY edge must be flagged so an outward nudge can't clamp.
    # 1920x1080 monitor; last valid pixel is 1919 / 1079.
    assert win32_input._is_near_edge(0, 540, 0, 0, 1920, 1080, 5) is True      # left edge (dist 0)
    assert win32_input._is_near_edge(1919, 540, 0, 0, 1920, 1080, 5) is True   # right edge (dist 0)
    assert win32_input._is_near_edge(960, 0, 0, 0, 1920, 1080, 5) is True      # top edge
    assert win32_input._is_near_edge(960, 1079, 0, 0, 1920, 1080, 5) is True   # bottom edge


def test_is_near_edge_boundary_is_strict_less_than_margin():
    # Why: the threshold is "distance < margin" (strict). At exactly `margin` away the outward 5px nudge
    # lands on the last valid pixel and does NOT clamp, so it must count as NOT near.
    assert win32_input._is_near_edge(1914, 540, 0, 0, 1920, 1080, 5) is False  # dist (1919-1914)=5, not < 5
    assert win32_input._is_near_edge(1915, 540, 0, 0, 1920, 1080, 5) is True   # dist 4 < 5
    assert win32_input._is_near_edge(960, 540, 0, 0, 1920, 1080, 5) is False   # center, clear of all edges
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_win32_input.py::test_rect_center_of_monitor tests/test_win32_input.py::test_is_near_edge_true_at_each_edge tests/test_win32_input.py::test_is_near_edge_boundary_is_strict_less_than_margin -v`
Expected: FAIL — `AttributeError: module 'win32_input' has no attribute '_rect_center'` (and `_is_near_edge`).

- [ ] **Step 3: Write the helpers**

In `win32_input.py`, insert directly before `def move_mouse_relative`:

```python
def _rect_center(left: int, top: int, right: int, bottom: int) -> tuple[int, int]:
    """Return the center pixel (cx, cy) of a monitor RECT. right/bottom are exclusive (Windows convention)."""
    return ((left + right) // 2, (top + bottom) // 2)


def _is_near_edge(x: int, y: int, left: int, top: int, right: int, bottom: int, margin: int) -> bool:
    """Return True if (x, y) is within `margin` pixels of any edge of the RECT (left, top, right, bottom).

    right/bottom are exclusive (Windows convention), so the last valid pixel is right-1 / bottom-1. A distance
    strictly less than `margin` counts as near: at exactly `margin` away an outward `margin`-px nudge still
    lands on a valid pixel and does not clamp.
    """
    return (
        (x - left) < margin
        or ((right - 1) - x) < margin
        or (y - top) < margin
        or ((bottom - 1) - y) < margin
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_win32_input.py -k "rect_center or near_edge" -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add win32_input.py tests/test_win32_input.py
git commit -m "feat: add pure monitor-geometry helpers (_rect_center, _is_near_edge)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Win32 monitor bounds (`get_monitor_bounds`)

**Files:**
- Modify: `win32_input.py` — add `MONITORINFO` struct + constant near the other structs/constants; add `MonitorFromPoint`/`GetMonitorInfoW` argtypes near the other argtype blocks (after the `SetCursorPos` block, ~line 87); add `get_monitor_bounds` after `_is_near_edge`; add a DPI note to the module docstring.
- Test: `tests/test_win32_input.py`

**Interfaces:**
- Consumes: nothing (independent Win32 plumbing).
- Produces: `get_monitor_bounds(x: int, y: int) -> tuple[int, int, int, int] | None` returning `(left, top, right, bottom)` in physical pixels, or `None` on failure.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_win32_input.py`:

```python
def test_get_monitor_bounds_contains_cursor():
    # Why: the recenter math depends on a monitor rect that actually contains the cursor, with the cursor
    # strictly inside the exclusive right/bottom bounds. A wrong rect would recenter onto the wrong monitor.
    x, y = win32_input.get_cursor_pos()
    bounds = win32_input.get_monitor_bounds(x, y)
    assert bounds is not None
    left, top, right, bottom = bounds
    assert left < right and top < bottom
    assert left <= x < right and top <= y < bottom
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_win32_input.py::test_get_monitor_bounds_contains_cursor -v`
Expected: FAIL — `AttributeError: module 'win32_input' has no attribute 'get_monitor_bounds'`.

- [ ] **Step 3: Add the struct, constant, argtypes, and function**

In `win32_input.py`, add the constant alongside the other Win32 constants (near `SM_*` block):

```python
MONITOR_DEFAULTTONEAREST = 2
```

Add the `MONITORINFO` struct after the `INPUT` class (it reuses `wintypes.RECT`):

```python
class MONITORINFO(ctypes.Structure):
    """Win32 MONITORINFO. cbSize must be set to sizeof(MONITORINFO) before GetMonitorInfoW is called."""

    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),   # full monitor rect in virtual-screen pixels; right/bottom exclusive
        ("rcWork", wintypes.RECT),      # work area (unused here)
        ("dwFlags", wintypes.DWORD),
    ]
```

Add argtypes after the `SetCursorPos` block (~line 87). `HMONITOR` is a pointer-sized handle, so use `c_void_p`; `POINT` is passed by value:

```python
# MonitorFromPoint(POINT pt, DWORD dwFlags) -> HMONITOR
user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
user32.MonitorFromPoint.restype = ctypes.c_void_p

# GetMonitorInfoW(HMONITOR hMonitor, LPMONITORINFO lpmi) -> BOOL
user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
user32.GetMonitorInfoW.restype = wintypes.BOOL
```

Add the function after `_is_near_edge`:

```python
def get_monitor_bounds(x: int, y: int) -> tuple[int, int, int, int] | None:
    """Return (left, top, right, bottom) of the monitor containing (x, y), in physical pixels.

    Uses MonitorFromPoint with MONITOR_DEFAULTTONEAREST, so a point just off every monitor still resolves to
    the nearest one (the returned handle is therefore always valid). right/bottom are exclusive (Windows RECT
    convention). Returns None if GetMonitorInfoW fails.

    The coordinates are physical pixels only when the process is per-monitor DPI aware; the GUI gets this from
    Qt 6's default (Per-Monitor-DPI-Aware-V2). Callers must not mix this rect with Qt logical coordinates.
    """
    monitor = user32.MonitorFromPoint(wintypes.POINT(x, y), MONITOR_DEFAULTTONEAREST)
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return None
    rect = info.rcMonitor
    return (rect.left, rect.top, rect.right, rect.bottom)
```

Append to the module docstring (after the SendInput rationale paragraph, before the closing `"""`):

```
Monitor geometry (get_monitor_bounds) is reported in physical pixels, which holds because the GUI process is
per-monitor DPI aware via Qt 6's default (Per-Monitor-DPI-Aware-V2). Do not mix these with Qt logical coords.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_win32_input.py::test_get_monitor_bounds_contains_cursor -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add win32_input.py tests/test_win32_input.py
git commit -m "feat: add get_monitor_bounds (MonitorFromPoint + GetMonitorInfo)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `ensure_off_edge` orchestrator

**Files:**
- Modify: `win32_input.py` — add `ensure_off_edge` after `move_mouse_relative`.
- Test: `tests/test_win32_input.py`

**Interfaces:**
- Consumes: `get_cursor_pos`, `get_monitor_bounds`, `_is_near_edge`, `_rect_center`, `move_mouse_relative`.
- Produces: `ensure_off_edge(margin: int) -> str | None` — `None` if recentered or already clear; error string on failure (same contract as `move_mouse_relative`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_win32_input.py`:

```python
def test_ensure_off_edge_moves_cursor_away_from_corner():
    # Why: this is the core safety — a cursor pinned in a monitor corner (within STEP_PIXELS of two edges)
    # must be recentered so the next outward nudge cannot clamp. Restores the original position afterward.
    start = win32_input.get_cursor_pos()
    left, top, right, bottom = win32_input.get_monitor_bounds(*start)
    try:
        # Park the cursor exactly in the monitor's top-left corner.
        win32_input.move_mouse_relative(left - start[0], top - start[1])
        corner_x, corner_y = win32_input.get_cursor_pos()
        assert win32_input._is_near_edge(corner_x, corner_y, left, top, right, bottom, 5) is True
        # Recenter and confirm the cursor is now clear of every edge.
        assert win32_input.ensure_off_edge(5) is None
        after_x, after_y = win32_input.get_cursor_pos()
        assert win32_input._is_near_edge(after_x, after_y, left, top, right, bottom, 5) is False
    finally:
        now = win32_input.get_cursor_pos()
        win32_input.move_mouse_relative(start[0] - now[0], start[1] - now[1])


def test_ensure_off_edge_is_noop_when_clear_of_edges():
    # Why: when already clear of every edge, ensure_off_edge must not move the cursor (no needless jump).
    start = win32_input.get_cursor_pos()
    center_x, center_y = win32_input._rect_center(*win32_input.get_monitor_bounds(*start))
    try:
        win32_input.move_mouse_relative(center_x - start[0], center_y - start[1])
        centered = win32_input.get_cursor_pos()
        assert win32_input.ensure_off_edge(5) is None
        assert win32_input.get_cursor_pos() == centered  # unchanged
    finally:
        now = win32_input.get_cursor_pos()
        win32_input.move_mouse_relative(start[0] - now[0], start[1] - now[1])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_win32_input.py -k ensure_off_edge -v`
Expected: FAIL — `AttributeError: module 'win32_input' has no attribute 'ensure_off_edge'`.

- [ ] **Step 3: Write `ensure_off_edge`**

In `win32_input.py`, add after `move_mouse_relative`:

```python
def ensure_off_edge(margin: int) -> str | None:
    """If the cursor is within `margin` pixels of any edge of its monitor, move it to that monitor's center.

    A nudge fired while the cursor sits at a screen edge is clamped by Windows (no displacement), which both
    fails to move the cursor and breaks the U->R->D->L cycle's zero-net-drift invariant. Recentering first
    guarantees the subsequent nudge has room to move. The recenter reuses move_mouse_relative, so it goes
    through the same absolute-SendInput + SetCursorPos-snap path and likewise resets the system idle timer.

    Args:
        margin: edge-proximity threshold in pixels; a distance strictly less than this triggers a recenter.

    Returns:
        None if the cursor was recentered or was already clear of every edge. On failure, a human-readable
        error string (same contract as move_mouse_relative): the cursor could not be read, the monitor could
        not be resolved, or the recenter move itself failed.
    """
    try:
        x, y = get_cursor_pos()
    except OSError as exc:
        return f"GetCursorPos failed: {exc}"
    bounds = get_monitor_bounds(x, y)
    if bounds is None:
        return "GetMonitorInfo failed: could not resolve the monitor under the cursor"
    left, top, right, bottom = bounds
    if not _is_near_edge(x, y, left, top, right, bottom, margin):
        return None
    center_x, center_y = _rect_center(left, top, right, bottom)
    return move_mouse_relative(center_x - x, center_y - y)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_win32_input.py -k ensure_off_edge -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add win32_input.py tests/test_win32_input.py
git commit -m "feat: add ensure_off_edge to recenter the cursor off a screen edge

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Wire `ensure_off_edge` into the nudge flow (GUI)

**Files:**
- Modify: `gui.py` — import `STEP_PIXELS`; call `ensure_off_edge(STEP_PIXELS)` in `_on_tick` before the directional nudge.

**Interfaces:**
- Consumes: `win32_input.ensure_off_edge`, `controller.STEP_PIXELS`.
- Produces: nothing (integration only).

**Note:** This project has no Qt/GUI unit-test harness (only `controller` and `win32_input` are unit-tested), matching the existing convention. Verification here is "the full existing suite still passes" plus the code-level check below; no GUI test is added.

- [ ] **Step 1: Add `STEP_PIXELS` to the controller import**

In `gui.py`, change the import line (~line 13):

```python
from controller import DETECT_INTERVAL_SEC, IDLE_THRESHOLD, IdleNudgeController
```

to:

```python
from controller import DETECT_INTERVAL_SEC, IDLE_THRESHOLD, STEP_PIXELS, IdleNudgeController
```

- [ ] **Step 2: Insert the recenter guard in `_on_tick`**

In `gui.py`, in `_on_tick`, between the `direction is None` early-return and the `move_mouse_relative` call, insert the guard so the block reads:

```python
        direction = self._controller.on_tick(position)
        if direction is None:
            return

        # Before nudging, make sure the cursor isn't pinned to a screen edge: an outward 5px nudge there
        # would be clamped (no movement, and the U->R->D->L cycle would no longer cancel to zero net drift).
        edge_error = win32_input.ensure_off_edge(STEP_PIXELS)
        if edge_error is not None:
            # Proceed anyway: a possibly-clamped nudge is no worse than before this guard existed.
            self.log(f"WARN: recenter failed: {edge_error}")

        move_error = win32_input.move_mouse_relative(direction.dx, direction.dy)
        if move_error is not None:
            self.log(f"WARN: mouse not moved: {move_error}")
            return
```

- [ ] **Step 3: Verify the module imports and the full suite still passes**

Run: `uv run python -c "import gui"`
Expected: no output, exit 0 (import succeeds — confirms `STEP_PIXELS` import and syntax are valid).

Run: `uv run pytest -v`
Expected: PASS — all tests, including the new geometry/ensure_off_edge tests from Tasks 1–3 and the existing `controller` tests.

- [ ] **Step 4: Commit**

```bash
git add gui.py
git commit -m "feat: recenter cursor off screen edges before nudging

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Strengthen `test_full_cycle_returns_to_origin_exactly`

**Files:**
- Modify: `tests/test_win32_input.py` — recenter before the cycle so it can't start at an edge.

**Interfaces:**
- Consumes: `win32_input.ensure_off_edge`.
- Produces: nothing.

- [ ] **Step 1: Update the test to recenter first**

In `tests/test_win32_input.py`, replace `test_full_cycle_returns_to_origin_exactly` with:

```python
def test_full_cycle_returns_to_origin_exactly():
    # Why: requirement #2 — a U->R->D->L cycle of 5px nudges must return to the EXACT origin (no drift).
    # First recenter off any edge: if the cursor were parked at a screen boundary, the outward nudge would
    # clamp and the cycle would NOT return to origin — an environmental failure unrelated to the move logic.
    # ensure_off_edge is a no-op when already clear of edges, so this is safe wherever the cursor starts.
    win32_input.ensure_off_edge(5)
    start = win32_input.get_cursor_pos()
    try:
        for dx, dy in [(0, -5), (5, 0), (0, 5), (-5, 0)]:
            assert win32_input.move_mouse_relative(dx, dy) is None
        assert win32_input.get_cursor_pos() == start
    finally:
        now = win32_input.get_cursor_pos()
        win32_input.move_mouse_relative(start[0] - now[0], start[1] - now[1])
```

- [ ] **Step 2: Run the test to verify it passes deterministically**

Run: `uv run pytest tests/test_win32_input.py::test_full_cycle_returns_to_origin_exactly -v`
Expected: PASS (no longer dependent on where the cursor was parked).

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest -v`
Expected: PASS — all tests.

- [ ] **Step 4: Commit**

```bash
git add tests/test_win32_input.py
git commit -m "test: recenter off edge before full-cycle return-to-origin assertion

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Documentation (README)

**Files:**
- Modify: `README.md` — note edge-recenter behavior and the new Win32 calls.

**Interfaces:**
- Consumes: nothing.
- Produces: nothing.

- [ ] **Step 1: Update the behavior description**

In `README.md`, find the sentence describing the nudge (the "nudges the cursor 5 px, cycling Up → Right → Down → Left" description near the top) and append a sentence:

```
Before each nudge, if the cursor is within 5 px of any edge of its current monitor, it is first moved to that
monitor's center so the nudge cannot be clamped at the screen boundary.
```

- [ ] **Step 2: Update the `win32_input.py` line in the Project Structure list**

In `README.md`, change the `win32_input.py` bullet (~line 49):

```
- `win32_input.py` — thin `ctypes` wrappers: `GetCursorPos`, `SendInput`, `SetThreadExecutionState`.
```

to:

```
- `win32_input.py` — thin `ctypes` wrappers: `GetCursorPos`, `SendInput`, `MonitorFromPoint`/`GetMonitorInfo` (edge recenter), `SetThreadExecutionState`.
```

- [ ] **Step 3: Verify the suite is still green (no code touched, sanity only)**

Run: `uv run pytest -q`
Expected: PASS — all tests.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: describe edge-recenter behavior and new Win32 calls

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- §2 decision 1 (before every nudge) → Task 4 (guard in `_on_tick`, runs each nudge).
- §2 decision 2 (near = distance < STEP_PIXELS, strict) → Task 1 `_is_near_edge` + boundary test.
- §2 decision 3 (monitor under cursor) → Task 2 `get_monitor_bounds` via `MonitorFromPoint`.
- §2 decision 4 (same-tick recenter then nudge) → Task 4 (guard precedes the existing nudge call; no early return on success).
- §2 decision 5 / §3 (DPI: rely on Qt per-monitor-v2) → Task 2 docstring + module docstring note; no DPI code.
- §2 decision 6 (failure → log WARN, proceed) → Task 4 (`edge_error` logged, no return).
- §2 decision 7 (layering; controller untouched) → helpers/orchestrator in `win32_input.py`; `controller.py` not modified.
- §4 functions → Tasks 1–3. §5 GUI → Task 4. §6 tests → Tasks 1–3 (helpers, bounds, ensure_off_edge) + Task 5 (strengthened cycle).
- §7 out of scope respected (no controller change, no move mechanism change, no DPI API, no new dependency).

**Placeholder scan:** none — every code/test step shows full content; every command has an expected result.

**Type consistency:** `_rect_center`/`_is_near_edge`/`get_monitor_bounds`/`ensure_off_edge` signatures are identical across the tasks that define and consume them; `ensure_off_edge` returns `str | None`, matching how Task 4 checks `if edge_error is not None`. `STEP_PIXELS` (= 5) is imported in Task 4; tests use the literal `5` to match the existing test style (the cycle deltas are already hard-coded `5`).
