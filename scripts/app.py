from __future__ import annotations

import asyncio
import ctypes
import logging
import sys
from importlib.resources import as_file, files

from grande_alpha import __version__


def _set_windows_app_identity() -> None:
    """Give Windows a stable taskbar identity so the packaged logo is used consistently."""
    if sys.platform != "win32":
        return
    try:
        from grande_alpha.desktop.windows_shortcut import WINDOWS_APP_USER_MODEL_ID

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_USER_MODEL_ID)
    except (AttributeError, OSError):
        logging.getLogger(__name__).warning("Windows app identity could not be registered")


def main() -> int:
    if "--version" in sys.argv:
        print(f"GRANDE Alpha {__version__}")
        return 0
    # A frozen windowed build hosts the hidden worker through the same executable.
    # Dispatch before importing Qt so the worker process never creates desktop UI.
    if "--worker" in sys.argv:
        from grande_alpha.execution.worker_process import main as worker_main

        worker_argv = [argument for argument in sys.argv[1:] if argument != "--worker"]
        return worker_main(worker_argv)
    try:
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QApplication, QMessageBox
        from qasync import QEventLoop

        from grande_alpha.ui.session_window import SessionWindow
        from grande_alpha.ui.themes import apply_application_theme, saved_theme
    except ModuleNotFoundError as exc:
        missing_module = exc.name or ""
        if missing_module in {"pyqtgraph", "qasync"} or missing_module.startswith("PySide6"):
            print(
                "The GRANDE Alpha desktop requires the optional desktop dependencies. "
                "Install with 'pip install grande-alpha[desktop]'.",
                file=sys.stderr,
            )
            return 2
        raise
    from grande_alpha.configuration.config import ensure_data_dir
    from grande_alpha.execution.process_lock import ProcessLock

    qt_argv = list(sys.argv)
    runtime_data_dir = ensure_data_dir()
    _set_windows_app_identity()
    logging.basicConfig(
        filename=runtime_data_dir / "grande_alpha.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    app = QApplication(qt_argv)
    app.setApplicationName("GRANDE Alpha")
    app.setApplicationDisplayName("GRANDE Alpha")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("GRANDE Alpha")
    app.setOrganizationDomain("local.grandealpha")
    apply_application_theme(saved_theme())
    icon_resource = files("grande_alpha.assets").joinpath("app-icon.png")
    with as_file(icon_resource) as icon_path:
        app.setWindowIcon(QIcon(str(icon_path)))
    # The independent worker owns app.lock.  A separate desktop lock prevents
    # duplicate windows without blocking or impersonating the worker.
    instance_lock = ProcessLock(runtime_data_dir / "desktop.lock")
    if not instance_lock.acquire(timeout_seconds=0.1):
        logging.warning("A second GRANDE Alpha instance was rejected")
        return 2
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)
    try:
        window = SessionWindow(runtime_data_dir)
        window.setWindowIcon(app.windowIcon())
        window.show()
        with loop:
            loop.run_forever()
        return 0
    except Exception as exc:
        logging.exception("Fatal startup error")
        QMessageBox.critical(None, "GRANDE Alpha failed to start", str(exc))
        return 1
    finally:
        instance_lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
