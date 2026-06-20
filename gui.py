"""PySide6 main window: Start/Stop buttons, log view, the 60s sampling timer, and file logging."""
from __future__ import annotations

import os
from datetime import datetime

from PySide6.QtCore import QTimer, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QHBoxLayout, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

import win32_input
from controller import DETECT_INTERVAL_SEC, IDLE_THRESHOLD, IdleNudgeController


class MainWindow(QWidget):
    """Top-level window wiring the sampling timer to the idle controller and the Win32 layer."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Anti-Idle")
        self.resize(480, 360)

        self._controller = IdleNudgeController()
        self._timer = QTimer(self)
        self._timer.setInterval(DETECT_INTERVAL_SEC * 1000)
        self._timer.timeout.connect(self._on_tick)

        self._log_file = self._open_log_file()  # file handle, or None if it couldn't be opened

        self._start_button = QPushButton("Start")
        self._stop_button = QPushButton("Stop")
        self._stop_button.setEnabled(False)  # nothing to stop until Start is pressed
        self._start_button.clicked.connect(self._on_start)
        self._stop_button.clicked.connect(self._on_stop)

        self._log_view = QPlainTextEdit()
        self._log_view.setReadOnly(True)

        button_row = QHBoxLayout()
        button_row.addWidget(self._start_button)
        button_row.addWidget(self._stop_button)

        layout = QVBoxLayout(self)
        layout.addLayout(button_row)
        layout.addWidget(self._log_view)

    def _open_log_file(self):
        """Open a per-run append-mode log file under logs/. Returns the handle, or None on failure."""
        try:
            os.makedirs("logs", exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            return open(os.path.join("logs", f"antiidle_{stamp}.log"), "a", encoding="utf-8")
        except OSError:
            return None

    def log(self, message: str) -> None:
        """Append a timestamped line to the GUI log view and the log file (if open)."""
        line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {message}"
        self._log_view.appendPlainText(line)
        if self._log_file is not None:
            try:
                self._log_file.write(line + "\n")
                self._log_file.flush()
            except OSError:
                # Fail loud but keep running with GUI-only logging.
                warn = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  WARN: failed to write log file."
                self._log_view.appendPlainText(warn)
                self._log_file = None

    @Slot()
    def _on_start(self) -> None:
        """Start monitoring: reset the controller, request keep-awake, start the timer."""
        self._controller.reset()
        if not win32_input.set_keep_awake():
            self.log("WARN: SetThreadExecutionState failed; relying on mouse nudge only.")
        self._timer.start()
        self._start_button.setEnabled(False)
        self._stop_button.setEnabled(True)
        self.log("Started monitoring.")

    @Slot()
    def _on_stop(self) -> None:
        """Stop monitoring: stop the timer and release keep-awake."""
        self._timer.stop()
        if not win32_input.clear_keep_awake():
            self.log("WARN: SetThreadExecutionState (clear) failed.")
        self._start_button.setEnabled(True)
        self._stop_button.setEnabled(False)
        self.log("Stopped monitoring.")

    @Slot()
    def _on_tick(self) -> None:
        """One sampling cycle: read cursor, ask controller, nudge + log if the idle threshold is hit."""
        try:
            position = win32_input.get_cursor_pos()
        except OSError:
            self.log("WARN: GetCursorPos failed; skipping this sample.")
            return

        direction = self._controller.on_tick(position)
        if direction is None:
            return

        if not win32_input.move_mouse_relative(direction.dx, direction.dy):
            self.log("WARN: SendInput failed; mouse not moved.")
            return

        # Feed the controller the real post-move position so the next nudge waits a full cycle.
        try:
            self._controller.sync_position(win32_input.get_cursor_pos())
        except OSError:
            self.log("WARN: GetCursorPos failed after nudge.")
        self.log(f"Idle {IDLE_THRESHOLD} reads detected → moved mouse {direction.name} (5px).")

    def closeEvent(self, event: QCloseEvent) -> None:
        """Release keep-awake and close the log file when the window closes."""
        self._timer.stop()
        # WARN before closing the log file so self.log() can still reach it.
        if not win32_input.clear_keep_awake():
            self.log("WARN: SetThreadExecutionState (clear) failed.")
        if self._log_file is not None:
            self._log_file.close()
        event.accept()
