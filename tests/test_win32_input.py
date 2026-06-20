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
    assert win32_input.move_mouse_relative(0, 0) is None  # None == success; a string would be the failure reason


def test_keep_awake_roundtrip_succeeds():
    # Why: confirms SetThreadExecutionState is wired correctly in both directions.
    assert win32_input.set_keep_awake() is True
    assert win32_input.clear_keep_awake() is True


def test_pixel_to_normalized_maps_corners():
    # Why: the absolute-move math must map the virtual-desktop origin to 0 and the
    # far corner to 65535; a wrong formula would mis-place every nudge.
    assert win32_input._pixel_to_normalized(0, 0, 0, 0, 1920, 1080) == (0, 0)
    assert win32_input._pixel_to_normalized(1919, 1079, 0, 0, 1920, 1080) == (65535, 65535)


def test_pixel_to_normalized_clamps_out_of_range():
    # Why: a nudge near the desktop edge can target a pixel just outside it; the
    # normalized result must stay within [0, 65535] (SendInput is undefined otherwise).
    assert win32_input._pixel_to_normalized(-50, -50, 0, 0, 1920, 1080) == (0, 0)
    assert win32_input._pixel_to_normalized(5000, 5000, 0, 0, 1920, 1080) == (65535, 65535)


def test_move_physically_displaces_cursor():
    # Why: relative-mickey SendInput is acceleration-scaled and rounded a 5px request
    # to 0px (no movement at all). Assert the cursor ACTUALLY displaces ~5px so this
    # regression cannot return. Restores the original position afterward.
    start = win32_input.get_cursor_pos()
    try:
        assert win32_input.move_mouse_relative(0, -5) is None
        after = win32_input.get_cursor_pos()
        moved_up = start[1] - after[1]  # UP is -y, so start.y - after.y is ~+5
        assert 4 <= moved_up <= 6, f"expected ~5px upward move, got {moved_up}"
    finally:
        # Restore exactly to the starting position regardless of rounding above.
        now = win32_input.get_cursor_pos()
        win32_input.move_mouse_relative(start[0] - now[0], start[1] - now[1])


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


def test_get_monitor_bounds_contains_cursor():
    # Why: the recenter math depends on a monitor rect that actually contains the cursor, with the cursor
    # strictly inside the exclusive right/bottom bounds. A wrong rect would recenter onto the wrong monitor.
    x, y = win32_input.get_cursor_pos()
    bounds = win32_input.get_monitor_bounds(x, y)
    assert bounds is not None
    left, top, right, bottom = bounds
    assert left < right and top < bottom
    assert left <= x < right and top <= y < bottom


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
