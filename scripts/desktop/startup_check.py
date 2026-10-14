"""Exercise the packaged desktop with disposable data and no external connections."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from importlib.resources import as_file, files
from pathlib import Path


def check_desktop() -> dict:
    # This diagnostic must never open an operator window, worker or broker session.
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from grande_alpha.ui.session_window import SessionWindow
    from grande_alpha.ui.themes import apply_application_theme

    app = QApplication([])
    apply_application_theme("light")
    with as_file(files("grande_alpha.assets").joinpath("app-icon.png")) as icon_path:
        icon = QIcon(str(icon_path))
        if icon.isNull():
            raise RuntimeError("The packaged app icon is missing")
        app.setWindowIcon(icon)
    with tempfile.TemporaryDirectory(prefix="grande-desktop-check-") as scratch:
        window = SessionWindow(Path(scratch), auto_probe=False)
        window.status_timer.stop()
        window.show()
        for _ in range(3):
            app.processEvents()
        if not window.desk.isVisible() or window._worker_seen or window._tasks:
            raise RuntimeError("Desktop startup did not remain local and idle")
        window._edit_candidate(new=True)
        editor = window._candidate_editor
        editor.resize(370, 650)
        for _ in range(3):
            app.processEvents()
        if editor.tabs.widget(0).horizontalScrollBar().maximum() != 0:
            raise RuntimeError("The packaged setup form overflows at narrow width")
        editor.reject()
        window._show_strategy_comparison()
        comparison = window._strategy_dialog
        comparison.resize(370, 620)
        for _ in range(3):
            app.processEvents()
        if comparison.run_button.isEnabled() or window._tasks:
            raise RuntimeError("The strategy comparison unexpectedly started work on opening")
        comparison.reject()
        window._closing = True
        window.close()
        app.processEvents()
        if any(Path(scratch).iterdir()):
            raise RuntimeError("Desktop check unexpectedly wrote runtime data")
    return {"ok": True, "desktop_created": True, "worker_started": False,
            "broker_connected": False, "model_requested": False}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Check GRANDE desktop using disposable data")
    parser.add_argument("--report", type=Path)
    arguments = parser.parse_args(argv)
    try:
        report = check_desktop()
    except Exception as error:
        report = {"ok": False, "error_type": type(error).__name__, "detail": str(error)[:2000]}
    if arguments.report is not None:
        arguments.report.parent.mkdir(parents=True, exist_ok=True)
        arguments.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if sys.stdout is not None:
        print(json.dumps(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
