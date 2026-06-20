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
