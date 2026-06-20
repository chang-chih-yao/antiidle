"""Entry point for the Anti-Idle GUI application."""
import sys

from PySide6.QtWidgets import QApplication

from gui import MainWindow


def main() -> None:
    """Launch the Anti-Idle GUI."""
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
