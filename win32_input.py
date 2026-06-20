"""Thin Win32 (ctypes) wrappers: read the cursor, nudge it, and keep Windows awake.

Windows-only. Each call sets argtypes/restype explicitly to avoid 64-bit pointer
truncation, and reports failure so callers can fail loud — by raising OSError, returning a
success boolean, or returning an error-detail string, per each function's docstring.

Why SendInput rather than SetCursorPos for the nudge: a synthesized absolute
MOUSEEVENTF_MOVE reliably resets the system idle timer (so the lock screen is
actually prevented), whereas SetCursorPos merely teleports the cursor and often
does not reset it. The move uses MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK so
that the displacement is exact pixels (relative mickey deltas are pointer-acceleration
scaled and a small value like 5px can round to 0px of actual movement).

Monitor geometry (get_monitor_bounds) is reported in physical pixels, which holds because the GUI process is
per-monitor DPI aware via Qt 6's default (Per-Monitor-DPI-Aware-V2). Do not mix these with Qt logical coords.
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

MONITOR_DEFAULTTONEAREST = 2

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


class MONITORINFO(ctypes.Structure):
    """Win32 MONITORINFO. cbSize must be set to sizeof(MONITORINFO) before GetMonitorInfoW is called."""

    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),   # full monitor rect in virtual-screen pixels; right/bottom exclusive
        ("rcWork", wintypes.RECT),      # work area (unused here)
        ("dwFlags", wintypes.DWORD),
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

# MonitorFromPoint(POINT pt, DWORD dwFlags) -> HMONITOR
user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
user32.MonitorFromPoint.restype = ctypes.c_void_p

# GetMonitorInfoW(HMONITOR hMonitor, LPMONITORINFO lpmi) -> BOOL
user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
user32.GetMonitorInfoW.restype = wintypes.BOOL


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


def move_mouse_relative(dx: int, dy: int) -> str | None:
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
        None on success. On failure, a human-readable string naming the Win32 call that failed and
        its formatted GetLastError detail, e.g. "SendInput failed (inserted 0/1 events): [WinError 5]
        Access is denied." so callers can log exactly why the nudge was dropped instead of a generic
        "SendInput failed". A string is returned rather than raised so the sampling loop survives a
        transient failure, and GetLastError is captured here (not by the caller) because the code is
        only valid immediately after the failing call — later Win32 calls overwrite the thread-local
        last-error, so it cannot be retrieved after this function returns.
    """
    try:
        cx, cy = get_cursor_pos()
    except OSError as exc:
        return f"GetCursorPos failed: {exc}"
    tx, ty = cx + dx, cy + dy
    vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    if vw < 2 or vh < 2:
        # GetSystemMetrics returns 0 on failure; without this guard (vw - 1) would be
        # negative and the cursor would be sent off-screen. Fail loud via the return contract.
        return f"virtual-screen metrics invalid: GetSystemMetrics returned width={vw}, height={vh}"
    nx, ny = _pixel_to_normalized(tx, ty, vx, vy, vw, vh)
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp.union.mi = MOUSEINPUT(dx=nx, dy=ny, mouseData=0,
                              dwFlags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
                              time=0, dwExtraInfo=0)
    sent = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    # Capture SendInput's GetLastError into a variable NOW: the SetCursorPos snap below is
    # another Win32 call and would overwrite the thread-local last-error that use_last_error=True
    # records for ctypes. (The snap's own error needs no variable — nothing follows it, so it can
    # be read inline at its return.)
    send_err = ctypes.get_last_error() if sent != 1 else 0
    # Snap to the exact target pixel. The absolute SendInput above resets the system idle
    # timer (real injected input) but its 65535-grid rounding can be off by ~1px, which would
    # accumulate and break the return-to-origin guarantee. SetCursorPos is pixel-exact.
    snapped = user32.SetCursorPos(tx, ty)
    # Report the first failure in call order. ctypes.WinError formats the numeric code into a
    # readable message via FormatMessage (e.g. "[WinError 5] Access is denied.").
    if sent != 1:
        return f"SendInput failed (inserted {sent}/1 events): {ctypes.WinError(send_err)}"
    if not snapped:
        return f"SetCursorPos snap failed: {ctypes.WinError(ctypes.get_last_error())}"
    return None


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
