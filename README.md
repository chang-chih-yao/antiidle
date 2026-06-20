# antiidle

A small Windows GUI that prevents the screen from locking / the system from going idle.

While running, it samples the cursor position once a minute. After 10 consecutive
identical samples (~10 minutes of no movement) it nudges the cursor 5px, cycling
through Up → Right → Down → Left so there is no net drift over a full cycle. It also
sets `SetThreadExecutionState` while active as a safety net. Each nudge and each
Start/Stop is logged to the window and to a per-run file under `logs/`.

## Requirements

- Windows 10 / 11
- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/)

## Install

```
uv sync
```

## Run

```
uv run python antiidle.py
```

Click **Start** to begin, **Stop** to halt. Logs appear in the window and in
`logs/antiidle_<timestamp>.log`.

## Test

```
uv run pytest
```

## Configuration

Edit the constants at the top of `controller.py`:

- `DETECT_INTERVAL_SEC` (default 60) — seconds between cursor samples
- `IDLE_THRESHOLD` (default 10) — consecutive identical samples before a nudge
- `STEP_PIXELS` (default 5) — nudge distance in pixels

## Architecture

- `controller.py` — pure idle-detection state machine (`IdleNudgeController`), no Qt/Win32; unit-tested.
- `win32_input.py` — thin `ctypes` wrappers: `GetCursorPos`, `SendInput`, `SetThreadExecutionState`.
- `gui.py` — `MainWindow`: buttons, log view, the sampling `QTimer`, file logging.
- `antiidle.py` — entry point.

## Build a standalone executable

```
uv run python -m nuitka --standalone --enable-plugin=pyside6 --windows-console-mode=disable antiidle.py
```
