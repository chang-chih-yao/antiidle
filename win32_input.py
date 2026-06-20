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
