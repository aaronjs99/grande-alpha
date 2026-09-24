"""Desktop controls for the independent background paper experiment."""
import sys

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from grande_alpha.paper_worker import URL, read_status, start_background, stop_background


class PaperWorkerPanel(QGroupBox):
    def __init__(self, parent=None, *, blocked=False):
        super().__init__("$100 background paper experiment", parent)
        self.blocked = blocked
        layout = QVBoxLayout(self)
        self.detail = QLabel("A separate saved experiment · $10 total loss budget · virtual money only")
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)
        controls = QHBoxLayout()
        self.start_button = QPushButton("Start background paper")
        self.open_button = QPushButton("Open profit dashboard")
        self.stop_button = QPushButton("Stop background paper")
        for button in (self.start_button, self.open_button, self.stop_button):
            controls.addWidget(button)
        layout.addLayout(controls)
        self.start_button.clicked.connect(self.start)
        self.open_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(URL)))
        self.stop_button.clicked.connect(self.stop)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2000)
        self.open_when_ready = False
        self.refresh()

    def start(self):
        if self.blocked:
            return
        try:
            start_background()
            self.open_when_ready = True
            self.detail.setText("Starting… Saved broker sign-in is required. The profit dashboard will open when ready.")
        except Exception:
            self.detail.setText("Background paper requires a Python source installation. Check that installation and data-folder access.")

    def stop(self):
        stop_background()
        self.open_when_ready = False
        self.detail.setText("Stopping background paper. Its positions, costs and loss budget are saved.")

    def refresh(self):
        status = read_status()
        online = status.get("online", False)
        self.start_button.setEnabled(not online and not self.blocked and not getattr(sys, "frozen", False))
        self.start_button.setToolTip("Use a Python source installation for the background service" if getattr(sys, "frozen", False) else "")
        self.open_button.setEnabled(online)
        self.stop_button.setEnabled(online)
        if status:
            paper = status.get("paper")
            description = f"{'Background online' if online else 'Background offline'} · {status.get('phase', '')}"
            if paper:
                description += (f" · Net ${float(paper['net_profit']):+.2f} · Expenses ${float(paper['operating_expenses']):.2f}"
                                f" · Loss budget left ${float(paper['loss_remaining']):.2f}")
            self.detail.setText(description)
        if online and self.open_when_ready:
            self.open_when_ready = False
            QDesktopServices.openUrl(QUrl(URL))
