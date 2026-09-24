from __future__ import annotations

import re
from importlib import import_module

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QBrush, QColor, QPalette, QPen
from PySide6.QtWidgets import QApplication, QTableWidget

# Canonical legacy colors remain dark; originals are retained for lossless toggling.
LIGHT_COLORS = {
    "#0b1118": "#f1f3f5", "#081018": "#f1f3f5", "#0a141d": "#ffffff",
    "#e9f0f6": "#233541", "#223142": "#d4dee5", "#1b2b3b": "#e3f1eb",
    "#8fd3ff": "#21658a", "#101a24": "#ffffff", "#304357": "#ccd8e0",
    "#1f3446": "#d9eaf3", "#ffffff": "#233541", "#5d7182": "#788993",
    "#2c3c4b": "#d4dee5", "#111a24": "#ffffff", "#8fa4b8": "#5e7280",
    "#182634": "#ffffff", "#2c4155": "#c6d5de", "#213447": "#e6f0f5",
    "#596b7a": "#7b8c98", "#121b24": "#edf1f4", "#00c805": "#159d71",
    "#021004": "#ffffff", "#0e1720": "#ffffff", "#101c27": "#f6f9fb",
    "#244663": "#d9eee5", "#14202b": "#edf3f6", "#a9bac8": "#516a7a",
    "#00e507": "#167847", "#2b3b4b": "#c6d5de", "#d7e5f0": "#344f60",
    "#91a6b8": "#5e7280", "#ffd27a": "#825a12", "#2b2315": "#fff5de",
    "#6f5727": "#d8b87b", "#243648": "#ffffff", "#45617a": "#b4cbd9",
    "#15324a": "#e5f0f8", "#3478a4": "#a9c9df", "#142b3d": "#e5f0f8",
    "#315b78": "#a9c9df", "#17301f": "#e6f4e9", "#80e899": "#236e3b",
    "#376d45": "#98c6a5", "#65b9ff": "#246b9b", "#f2c14e": "#91650d",
    "#ff697d": "#b6314c", "#ffca7a": "#825a12", "#6688a3": "#54738a",
    "#00d407": "#168354", "#b9c2cc": "#627585", "#d9f1ff": "#23678b",
    "#fff1b8": "#91650d", "#ff9eb5": "#b6314c", "#f3f5f7": "#425567",
}
AGENT_DARK = {
    "#f1f3f5": "#0b1118", "#ffffff": "#14212c", "#27343e": "#e3ebf1",
    "#19252e": "#eef3f7", "#63747f": "#9fb2c0", "#78858e": "#9fb2c0",
    "#98651b": "#f0c67a", "#fff7e6": "#302818", "#eddfbc": "#695535",
    "#677780": "#a7b7c2", "#e1e6e9": "#2b3c49", "#17242c": "#eef3f7",
    "#627580": "#a3b5c2", "#7a878f": "#a7b8c6", "#1ca97a": "#40ce9b",
    "#425460": "#dbe5ed", "#dce3e7": "#314858", "#e9f5f0": "#193c32",
    "#87c9ae": "#3c8065", "#f0f2f4": "#17232c", "#929da4": "#899aa6",
    "#e2e7eb": "#293a47", "#deebe5": "#1b332c", "#78938a": "#8cab9d",
    "#526771": "#b1c4d1", "#f8fafb": "#101b24", "#d4dfe4": "#344957",
    "#43535f": "#c4d3de", "#edf0f2": "#263946", "#e0f2eb": "#204d3e",
    "#f0f3f5": "#243642", "#657782": "#a0b5c4", "#edf1f3": "#15232d",
    "#bcc9cf": "#4c6779", "#e6ecef": "#344957", "#e7ebee": "#344957",
    "#25323a": "#dce8f0", "#16845e": "#40ce9b", "#b74b63": "#ff91a9",
    "#788b97": "#adc0cd", "#a77924": "#e9bd69", "#566e7c": "#a8c3d4",
}
COLOR_ROLE = int(Qt.ItemDataRole.UserRole) + 197


def appearance_settings():
    return QSettings("GRANDEAlpha", "Appearance")


def saved_theme():
    value = appearance_settings().value("theme", "light")
    return value if value in ("light", "dark") else "light"


def current_theme():
    app = QApplication.instance()
    return (app.property("grandeTheme") if app else None) or "light"


def color(value, *, base="dark"):
    value = QColor(value).name()
    if current_theme() == base:
        return value
    return (LIGHT_COLORS if base == "dark" else AGENT_DARK).get(value, value)


def theme_css(source, *, base="dark"):
    return re.sub(r"#[0-9a-fA-F]{6}\b", lambda m: color(m.group(), base=base), source)


def set_widget_style(widget, source):
    widget.setProperty("grandeThemeStyle", source)
    widget.setStyleSheet(theme_css(source))


def set_item_foreground(item, value, *, base="dark"):
    value = QColor(value).name()
    item.setData(COLOR_ROLE, (value, base))
    item.setForeground(QColor(color(value, base=base)))


def apply_application_theme(theme):
    if theme not in ("light", "dark"):
        raise ValueError("Unknown appearance theme")
    app = QApplication.instance()
    if app.property("grandeTheme") == theme and app.styleSheet() == theme_css(STYLESHEET):
        return
    app.setProperty("grandeTheme", theme)
    palette = QPalette(app.palette())
    roles = {
        "Window": "#0b1118", "WindowText": "#e9f0f6", "Base": "#0e1720",
        "AlternateBase": "#101c27", "Text": "#e9f0f6", "Button": "#182634",
        "ButtonText": "#e9f0f6", "Highlight": "#244663", "HighlightedText": "#ffffff",
        "ToolTipBase": "#243648", "ToolTipText": "#e9f0f6", "Link": "#65b9ff",
    }
    for role, value in roles.items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(color(value)))
    app.setPalette(palette)
    app.setStyleSheet(theme_css(STYLESHEET))
    for widget in app.allWidgets():
        source = widget.property("grandeThemeStyle")
        if source is not None:
            widget.setStyleSheet(theme_css(source))
        if isinstance(widget, QTableWidget):
            for row in range(widget.rowCount()):
                for column in range(widget.columnCount()):
                    item = widget.item(row, column)
                    saved = item.data(COLOR_ROLE) if item else None
                    if saved:
                        set_item_foreground(item, saved[0], base=saved[1])


def theme_plot(plot):
    # Plotting belongs to the optional legacy research screens, not the
    # worker control panel or its packaged startup dependency graph.
    pg = import_module("pyqtgraph")
    plot.setBackground(color("#0e1720"))
    for name in ("left", "right", "top", "bottom"):
        axis = plot.getAxis(name)
        axis.setPen(pg.mkPen(color("#223142")))
        axis.setTextPen(pg.mkPen(color("#a9bac8")))
        if axis.labelText or axis.labelUnits:
            axis.setLabel(axis.labelText, units=axis.labelUnits, **{"color": color("#a9bac8")})
    for item in plot.getPlotItem().items:
        opts = getattr(item, "opts", {})
        if not hasattr(item, "_grande_theme_pens"):
            item._grande_theme_pens = {k: QPen(v) for k, v in opts.items() if k in ("pen", "symbolPen") and isinstance(v, QPen)}
            if isinstance(item, pg.InfiniteLine):
                item._grande_theme_pens["pen"] = QPen(item.pen)
            item._grande_theme_brushes = {k: QBrush(v) for k, v in opts.items() if k in ("brush", "symbolBrush") and isinstance(v, QBrush)}
        for key, original in item._grande_theme_pens.items():
            pen = QPen(original)
            pen.setColor(QColor(color(original.color())))
            getattr(item, "setPen" if key == "pen" else "setSymbolPen")(pen)
        for key, original in item._grande_theme_brushes.items():
            brush = QBrush(original)
            brush.setColor(QColor(color(original.color())))
            setter = getattr(item, "setBrush" if key == "brush" else "setSymbolBrush", None)
            if setter:
                setter(brush)
    legend = plot.getPlotItem().legend
    if legend:
        for _, label in legend.items:
            label.setText(label.text, color=color("#e9f0f6"))


STYLESHEET = """
QWidget { background: #0b1118; color: #e9f0f6; font-size: 10pt; }
QMainWindow { background: #081018; }
QMenuBar { background: #0a141d; border-bottom: 1px solid #223142; padding: 2px 5px; }
QMenuBar::item { background: transparent; padding: 6px 10px; border-radius: 4px; }
QMenuBar::item:selected { background: #1b2b3b; color: #8fd3ff; }
QMenu { background: #101a24; border: 1px solid #304357; padding: 5px; }
QMenu::item { padding: 7px 34px 7px 24px; border-radius: 4px; }
QMenu::item:selected { background: #1f3446; color: #ffffff; }
QMenu::item:disabled { color: #5d7182; }
QMenu::separator { height: 1px; background: #2c3c4b; margin: 5px 8px; }
QFrame#card { background: #111a24; border: 1px solid #223142; border-radius: 10px; }
QLabel#cardTitle { color: #8fa4b8; font-size: 9pt; }
QLabel#cardValue { font-size: 18pt; font-weight: 650; }
QLabel#dialogTitle { font-size: 17pt; font-weight: 650; }
QPushButton { background: #182634; border: 1px solid #2c4155; border-radius: 7px; padding: 8px 13px; }
QPushButton:hover { background: #213447; }
QPushButton:disabled { color: #596b7a; background: #121b24; }
QPushButton#primary { background: #00c805; border-color: #00c805; color: #021004; font-weight: 700; }
QPushButton#danger { background: #c62d42; border-color: #ec5266; color: white; font-weight: 700; }
QPushButton#flatten { background: #7f3d18; border-color: #c66a2e; color: white; }
QTableWidget { background: #0e1720; alternate-background-color: #101c27; border: 1px solid #223142; gridline-color: #223142; }
QTableWidget::item:selected { background: #244663; color: #ffffff; }
QHeaderView::section { background: #14202b; color: #a9bac8; padding: 6px; border: 0; border-right: 1px solid #223142; }
QTabWidget::pane { border: 1px solid #223142; }
QTabBar::tab { background: #111a24; padding: 9px 16px; }
QTabBar::tab:selected { background: #1b2b3b; color: #00e507; }
QLineEdit, QSpinBox, QDoubleSpinBox { background: #0e1720; border: 1px solid #2c4155; border-radius: 5px; padding: 6px; }
QCheckBox { spacing: 8px; }
QGroupBox { border: 1px solid #2b3b4b; border-radius: 9px; margin-top: 13px; padding-top: 12px; font-weight: 650; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 7px; color: #d7e5f0; }
QLabel#settingsDescription { color: #91a6b8; font-size: 9pt; }
QLabel#settingsStatus { border-radius: 6px; padding: 6px 9px; font-size: 9pt; font-weight: 700; }
QLabel#validationWarning { color: #ffd27a; background: #2b2315; border: 1px solid #6f5727; border-radius: 6px; padding: 8px; }
QToolTip { background: #243648; color: #e9f0f6; border: 1px solid #45617a; }
"""
STYLESHEET += """
QComboBox, QPlainTextEdit, QTextEdit { background: #0e1720; color: #e9f0f6; border: 1px solid #2c4155; padding: 5px; }
QComboBox QAbstractItemView { background: #0e1720; color: #e9f0f6; selection-background-color: #244663; }
QPushButton:focus { border: 2px solid #3478a4; }
QPushButton#danger, QPushButton#flatten { color: white; }
"""
