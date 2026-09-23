"""Quiet, high-contrast native theme; uses installed system fonts, not remote assets."""

THEME = """
QWidget { background: #10151c; color: #e7eae9; font-family: 'Segoe UI'; font-size: 10pt; }
QMainWindow { background: #10151c; }
QLabel { background: transparent; }
QMenuBar { background: #151c25; border-bottom: 1px solid #2b3541; padding: 2px 6px; }
QMenuBar::item { padding: 6px 10px; background: transparent; }
QMenuBar::item:selected, QMenu::item:selected { background: #283341; }
QMenu { background: #1a232e; border: 1px solid #364252; padding: 6px; }
QMenu::item { padding: 8px 28px 8px 18px; }
QMenu::item:disabled { color: #7e8996; }
QMenu::separator { height: 1px; background: #364252; margin: 5px; }
QFrame#workspaceNavigation { background: #151c25; border: 1px solid #283340; border-radius: 12px; }
QPushButton, QToolButton { background: #202b37; color: #e7eae9; border: 1px solid #354252;
 border-radius: 7px; padding: 8px 12px; }
QPushButton:hover, QToolButton:hover { background: #2b3948; border-color: #617081; }
QPushButton:focus, QToolButton:focus, QLineEdit:focus, QComboBox:focus { border: 1px solid #d2bc8e; }
QPushButton:disabled, QToolButton:disabled { color: #7c8896; background: #19222c; border-color: #2c3642; }
QPushButton#navigationItem { text-align: left; background: transparent; border: 1px solid transparent; }
QPushButton#navigationItem:checked { background: #2a3541; color: #e4d2ae; border-color: #46505b; }
QPushButton#navigationItem:focus { border-color: #d2bc8e; }
QPushButton#primary { background: #d2bc8e; border-color: #d2bc8e; color: #151a20; font-weight: 600; }
QPushButton#primary:disabled { background: #3b3b34; color: #9a9689; border-color: #49493e; }
QPushButton#danger { background: #38252b; color: #ffb3b9; border-color: #87505b; font-weight: 600; }
QPushButton#flatten { background: #302b23; color: #e5c48d; border-color: #776346; }
QFrame#card { background: #19222c; border: 1px solid #303b48; border-radius: 11px; }
QLabel#cardTitle { color: #a7b2bd; font-size: 9pt; }
QLabel#cardValue { color: #eef0e9; font-size: 19pt; font-weight: 600; }
QLabel#dialogTitle { font-size: 20pt; font-weight: 600; }
QTableWidget { background: #141d27; alternate-background-color: #19232e; border: 1px solid #303c49;
 gridline-color: #273341; selection-background-color: #36495d; }
QTableWidget::item { padding: 5px; }
QTableWidget::item:selected { background: #36495d; color: #ffffff; }
QHeaderView::section { background: #1c2733; color: #b8c3ce; padding: 8px; border: 0; border-bottom: 1px solid #364252; }
QTabWidget::pane { border: 0; }
QTabBar::tab { background: #19232d; padding: 10px 14px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { background: #26323e; color: #e4d2ae; border-bottom-color: #d2bc8e; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit { background: #141e28; border: 1px solid #394858;
 border-radius: 6px; padding: 6px; selection-background-color: #36495d; }
QComboBox QAbstractItemView { background: #19232e; selection-background-color: #36495d; }
QCheckBox { spacing: 8px; }
QGroupBox { background: #161f29; border: 1px solid #303c49; border-radius: 10px;
 margin-top: 14px; padding: 14px 8px 8px 8px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; color: #cbd3d9; }
QLabel#settingsDescription { color: #a0aebc; font-size: 9pt; }
QLabel#settingsStatus { border-radius: 6px; padding: 6px 9px; font-size: 9pt; font-weight: 600; }
QLabel#validationWarning { color: #e4c997; background: #302b22; border: 1px solid #695e49; border-radius: 8px; padding: 10px; }
QToolTip { background: #283442; color: #ffffff; border: 1px solid #64758a; padding: 5px; }
QScrollBar:vertical { background: #141c25; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #465565; border-radius: 5px; min-height: 28px; }
QScrollBar:horizontal { background: #141c25; height: 10px; margin: 0; }
QScrollBar::handle:horizontal { background: #465565; border-radius: 5px; min-width: 28px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QSplitter::handle { background: #26303b; }
"""
