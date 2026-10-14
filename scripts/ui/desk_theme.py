"""Antonio's Agent desk styling, shared by the desk and its setup dialogs."""

DASHBOARD_STYLE = """
QScrollArea { background: #f1f3f5; border: none; }
QWidget#agentCanvas { background: #f1f3f5; }
QDialog, QMessageBox { background: #f1f3f5; }
QComboBox QAbstractItemView { background: #ffffff; color: #27343e; selection-background-color: #e0f2eb; selection-color: #17242c; }
QToolTip { background: #ffffff; color: #27343e; border: 1px solid #d4dfe4; }
QWidget { color: #27343e; font-family: 'Segoe UI', 'Arial'; font-size: 10pt; }
QLabel { background: transparent; border: none; }
QLabel#dashboardTitle { font-family: 'Menlo', 'Consolas', monospace; font-size: 20pt; font-weight: 800; color: #19252e; }
QLabel#eyebrow { color: #63747f; font-family: 'Menlo', 'Consolas', monospace; font-size: 9pt; letter-spacing: 2px; }
QLabel#modeBadge { color: #98651b; background: #fff7e6; border: 1px solid #eddfbc; border-radius: 6px; padding: 8px 12px; font-size: 9pt; font-weight: 700; }
QLabel#dashboardNotice { color: #677780; font-size: 9pt; }
QFrame#dashboardPanel { background: #ffffff; border: 1px solid #e1e6e9; border-radius: 8px; }
QLabel#metricValue { font-family: 'Menlo', 'Consolas', monospace; font-size: 25pt; font-weight: 700; color: #17242c; }
QLabel#metricCaption { color: #627580; font-size: 9pt; }
QLabel#sectionTitle { color: #7a878f; font-family: 'Menlo', 'Consolas', monospace; font-size: 10pt; font-weight: 600; letter-spacing: 2px; }
QLabel#chartValue { color: #1ca97a; font-family: 'Menlo', 'Consolas', monospace; font-size: 17pt; font-weight: 700; }
QPushButton { background: #ffffff; color: #425460; border: 1px solid #dce3e7; border-radius: 6px; padding: 9px 14px; font-size: 9pt; font-weight: 600; min-height: 18px; }
QPushButton:hover, QPushButton:checked { background: #e9f5f0; border-color: #87c9ae; }
QPushButton:disabled { background: #f0f2f4; color: #929da4; border-color: #e2e7eb; }
QPushButton#primary { background: #159d71; border-color: #159d71; color: white; }
QPushButton#primary:disabled { background: #deebe5; color: #78938a; border-color: #deebe5; }
QGroupBox { background: #ffffff; border: 1px solid #dce3e7; border-radius: 7px; margin-top: 16px; padding: 16px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 14px; color: #526771; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit { background: #f8fafb; color: #27343e; border: 1px solid #d4dfe4; border-radius: 4px; padding: 7px; min-height: 20px; }
QTableWidget { background: #ffffff; alternate-background-color: #f8fafb; color: #43535f; border: none; gridline-color: #edf0f2; selection-background-color: #e0f2eb; selection-color: #17242c; font-family: 'Menlo', 'Consolas', monospace; font-size: 9pt; }
QTableWidget::item { padding: 8px 5px; border-bottom: 1px solid #f0f3f5; }
QHeaderView::section { background: #f8fafb; color: #657782; border: none; padding: 9px 5px; font-size: 8pt; }
QScrollBar:vertical { background: #edf1f3; width: 8px; margin: 0; }
QScrollBar::handle:vertical { background: #bcc9cf; min-height: 30px; border-radius: 4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: #edf1f3; height: 8px; margin: 0; }
QScrollBar::handle:horizontal { background: #bcc9cf; min-width: 30px; border-radius: 4px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QWidget#agentChat { background: #ffffff; border: 1px solid #e1e6e9; border-radius: 10px; }
QWidget#chatMessages, QWidget#chatMessageRow, QScrollArea#chatTranscript { background: transparent; border: none; }
QLabel#chatTitle { font-size: 16pt; font-weight: 750; letter-spacing: 1px; }
QLabel#chatSpeaker { font-size: 8pt; font-weight: 650; color: #657782; }
QLabel#chatEmpty { color: #657782; padding: 12px; }
QFrame#chatBubble { background: #f8fafb; border: 1px solid #e1e6e9; border-radius: 12px; }
QFrame#chatBubble[speaker="user"] { background: #e0f2eb; border-color: #87c9ae; }
QFrame#chatProposal { background: #f8fafb; border: 1px solid #d4dfe4; border-radius: 8px; }
QPushButton#stopAgent { border-color: #b74b63; color: #b74b63; }
QPushButton#stopAgent:disabled { border-color: #e2e7eb; color: #929da4; }
QLabel#deskHeartbeat { color: #16845e; font-weight: 600; }
QLabel#deskHeartbeat[attention="true"] { color: #b74b63; }
QLabel#sourcePill { background: #f8fafb; border: 1px solid #d4dfe4; border-radius: 6px; padding: 6px 9px; font-size: 8pt; }
QLabel#deskClock { color: #627580; font-size: 8pt; }
QPushButton#rangeButton { padding: 3px 8px; min-height: 15px; font-size: 8pt; }
"""
