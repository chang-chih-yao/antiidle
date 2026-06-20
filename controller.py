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
