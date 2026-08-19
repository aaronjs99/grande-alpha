"""Reproducible off-screen capture for the public plan dialog."""

from __future__ import annotations

import argparse
from pathlib import Path

from PySide6.QtWidgets import QApplication

from grande_alpha.ui.main_window import STYLESHEET
from grande_alpha.ui.product_dialog import ProductPlansDialog


def capture(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLESHEET)
    dialog = ProductPlansDialog(upgrade_url="")
    dialog.resize(900, 680)
    dialog.show()
    app.processEvents()
    image = dialog.grab()
    if not image.save(str(path)):
        raise RuntimeError(f"Could not save {path}")
    dialog.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/images/product/community-and-pro-plans.png"),
    )
    args = parser.parse_args()
    capture(args.output)


if __name__ == "__main__":
    main()
