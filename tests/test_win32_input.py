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
