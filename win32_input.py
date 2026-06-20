"""Thin Win32 (ctypes) wrappers: read the cursor, nudge it, and keep Windows awake.

Windows-only. Each call sets argtypes/restype explicitly to avoid 64-bit pointer
truncation, and returns a success boolean (or the position) so callers can fail loud.

Why SendInput rather than SetCursorPos for the nudge: a synthesized absolute
MOUSEEVENTF_MOVE reliably resets the system idle timer (so the lock screen is
actually prevented), whereas SetCursorPos merely teleports the cursor and often
does not reset it. The move uses MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK so
that the displacement is exact pixels (relative mickey deltas are pointer-acceleration
scaled and a small value like 5px can round to 0px of actual movement).
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

# --- Win32 constants ---
INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

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

# GetSystemMetrics(int nIndex) -> int
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int

# SetCursorPos(int X, int Y) -> BOOL
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wintypes.BOOL


def get_cursor_pos() -> tuple[int, int]:
    """Return the current cursor position as (x, y).

    Raises:
        OSError: if GetCursorPos fails (raised via ctypes.WinError).
    """
    point = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        raise ctypes.WinError(ctypes.get_last_error())
    return (point.x, point.y)


def _pixel_to_normalized(px: int, py: int, vx: int, vy: int, vw: int, vh: int) -> tuple[int, int]:
    """Convert a screen pixel (px, py) to a 0..65535 absolute coordinate over the virtual desktop.

    Args:
        px, py: target pixel in screen coordinates.
        vx, vy: virtual-desktop origin (SM_X/Y VIRTUALSCREEN).
        vw, vh: virtual-desktop width/height in pixels (SM_CX/CY VIRTUALSCREEN), each >= 2.
            Precondition: vw >= 2 and vh >= 2 (callers must guard; the divisor is vw-1 / vh-1).

    Returns:
        (nx, ny) normalized 0..65535 coordinates for a MOUSEEVENTF_ABSOLUTE | VIRTUALDESK move.
    """
    nx = round((px - vx) * 65535 / (vw - 1))
    ny = round((py - vy) * 65535 / (vh - 1))
    # A nudge near the desktop edge can target a pixel just outside it; SendInput's
    # behavior for absolute coordinates outside [0, 65535] is undefined, so clamp.
    nx = max(0, min(65535, nx))
    ny = max(0, min(65535, ny))
    return nx, ny


def move_mouse_relative(dx: int, dy: int) -> bool:
    """Move the cursor by (dx, dy) pixels using an ABSOLUTE SendInput followed by a SetCursorPos snap.

    A RELATIVE MOUSEEVENTF_MOVE delta is in pointer-acceleration "mickeys", not pixels
    (a 5px request can round to 0px of movement). An absolute move bypasses acceleration so
    the displacement is exact, while remaining real injected input that resets the system
    idle timer (which SetCursorPos does not reliably do).

    The absolute SendInput resets the idle timer but its 65535-grid rounding can be off by ~1px,
    which accumulates into ~3px drift per U->R->D->L cycle. The SetCursorPos snap after the
    SendInput corrects the final pixel position to be exact, guaranteeing an exact return to
    origin over a full cycle.

    Returns:
        True iff exactly one input event was inserted (False if the cursor position could
        not be read).
    """
    try:
        cx, cy = get_cursor_pos()
    except OSError:
        return False
    tx, ty = cx + dx, cy + dy
    vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    if vw < 2 or vh < 2:
        # GetSystemMetrics returns 0 on failure; without this guard (vw - 1) would be
        # negative and the cursor would be sent off-screen. Fail loud via the bool contract.
        return False
    nx, ny = _pixel_to_normalized(tx, ty, vx, vy, vw, vh)
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp.union.mi = MOUSEINPUT(dx=nx, dy=ny, mouseData=0,
                              dwFlags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
                              time=0, dwExtraInfo=0)
    sent = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    # Snap to the exact target pixel. The absolute SendInput above resets the system idle
    # timer (real injected input) but its 65535-grid rounding can be off by ~1px, which would
    # accumulate and break the return-to-origin guarantee. SetCursorPos is pixel-exact.
    user32.SetCursorPos(tx, ty)
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
