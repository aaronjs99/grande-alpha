from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from grande_alpha.ui.session_components import PreciseDoubleSpinBox


class SessionPanelBuilder:
    def _build_monitor_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("card")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(11)

        heading = QLabel("Session monitor")
        heading.setObjectName("dialogTitle")
        self.monitor_summary = QLabel("Start a session to monitor it here.")
        self.monitor_summary.setObjectName("settingsDescription")
        self.monitor_summary.setWordWrap(True)

        cycle_heading = QLabel("Latest strategy check")
        cycle_heading.setObjectName("settingsDescription")
        self.monitor_cycle = QLabel("No cycle reported yet")
        self.monitor_cycle.setObjectName("dialogTitle")
        self.monitor_cycle.setWordWrap(True)
        self.monitor_cycle_time = QLabel("")
        self.monitor_cycle_time.setObjectName("settingsDescription")

        data_heading = QLabel("Market data")
        data_heading.setObjectName("settingsDescription")
        self.monitor_data = QLabel("No market snapshot reported yet")
        self.monitor_data.setWordWrap(True)

        self.monitor_note = QLabel(
            "A recorded broker response is not proof of a fill. Verify orders, fills, balances, "
            "and positions with Robinhood."
        )
        self.monitor_note.setObjectName("settingsDescription")
        self.monitor_note.setWordWrap(True)

        for widget in (
            heading,
            self.monitor_summary,
            cycle_heading,
            self.monitor_cycle,
            self.monitor_cycle_time,
            data_heading,
            self.monitor_data,
            self.monitor_note,
        ):
            layout.addWidget(widget)
        layout.addStretch()
        return panel

    def _build_flow_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("card")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(11)

        heading = QLabel("Start a bounded session")
        heading.setObjectName("dialogTitle")
        layout.addWidget(heading)

        self.connect_group = QGroupBox("1  Connect")
        connect_layout = QVBoxLayout(self.connect_group)
        connect_text = QLabel(
            "Open the broker sign-in only if needed, then verify the Agentic account exposed to the worker."
        )
        connect_text.setObjectName("settingsDescription")
        connect_text.setWordWrap(True)
        self.connect_button = QPushButton("Connect Robinhood")
        self.connect_button.setObjectName("primary")
        self.connect_button.clicked.connect(lambda: self._spawn(self._connect()))
        self.connect_detail = QLabel("Not connected")
        self.connect_detail.setObjectName("settingsDescription")
        self.connect_detail.setWordWrap(True)
        connect_layout.addWidget(connect_text)
        connect_layout.addWidget(self.connect_button)
        connect_layout.addWidget(self.connect_detail)
        layout.addWidget(self.connect_group)

        self.limits_group = QGroupBox("2  Set limits")
        limits_layout = QVBoxLayout(self.limits_group)
        help_text = QLabel(
            "Choose an existing strategy candidate, adjust only its risk envelope, then save a new copy. "
            "The strategy, account, symbols, time window, and research thresholds remain unchanged."
        )
        help_text.setObjectName("settingsDescription")
        help_text.setWordWrap(True)
        limits_layout.addWidget(help_text)

        candidate_row = QHBoxLayout()
        self.candidate_edit = QLineEdit()
        self.candidate_edit.setPlaceholderText("Strategy + limits candidate (.json)")
        self.candidate_edit.setAccessibleName("Strategy and limits candidate file")
        self.candidate_edit.editingFinished.connect(self._candidate_path_entered)
        candidate_button = QPushButton("Choose…")
        candidate_button.clicked.connect(self._choose_candidate)
        candidate_row.addWidget(self.candidate_edit, 1)
        candidate_row.addWidget(candidate_button)
        limits_layout.addLayout(candidate_row)

        self.limits_form = QFormLayout()
        self.limit_inputs: dict[str, QDoubleSpinBox | QSpinBox] = {}
        for key, label, kind in self.LIMIT_FIELDS:
            if kind == "integer":
                control: QDoubleSpinBox | QSpinBox = QSpinBox()
                control.setRange(1, 1_000_000_000)
            else:
                control = PreciseDoubleSpinBox()
                control.setDecimals(8)
                control.setRange(0.00000001, 1_000_000_000_000.0)
                if kind == "money":
                    control.setPrefix("$")
                elif kind == "bps":
                    control.setSuffix(" bps")
                elif kind == "seconds":
                    control.setSuffix(" s")
            control.setEnabled(False)
            control.setAccessibleName(label)
            control.valueChanged.connect(self._limit_changed)
            self.limit_inputs[key] = control
            self.limits_form.addRow(label, control)
        limits_layout.addLayout(self.limits_form)

        support_form = QFormLayout()
        self.authorization_edit = QLineEdit(str(self.data_dir / "mixed-authorization.json"))
        self.authorization_edit.setAccessibleName("Local exact-scope authorization file")
        self.authorization_edit.textChanged.connect(self._review_input_changed)
        authorization_row = self._path_row(self.authorization_edit, self._choose_authorization)
        support_form.addRow("Local permit", authorization_row)
        self.earnings_edit = QLineEdit(str(self.data_dir / "earnings.db"))
        self.earnings_edit.setAccessibleName("Local earnings observations database")
        self.earnings_edit.textChanged.connect(self._review_input_changed)
        earnings_row = self._path_row(self.earnings_edit, self._choose_earnings_database)
        support_form.addRow("Earnings data", earnings_row)
        self.poll_seconds = QDoubleSpinBox()
        self.poll_seconds.setRange(0.25, 3600.0)
        self.poll_seconds.setDecimals(2)
        self.poll_seconds.setValue(5.0)
        self.poll_seconds.setSuffix(" s")
        self.poll_seconds.valueChanged.connect(self._review_input_changed)
        support_form.addRow("Decision cadence", self.poll_seconds)
        limits_layout.addLayout(support_form)

        self.save_limits_button = QPushButton("Save limits as a new candidate…")
        self.save_limits_button.setEnabled(False)
        self.save_limits_button.clicked.connect(self._choose_limit_destination)
        self.limit_state = QLabel("Choose a candidate to load its current limits.")
        self.limit_state.setObjectName("settingsDescription")
        self.limit_state.setWordWrap(True)
        limits_layout.addWidget(self.save_limits_button)
        limits_layout.addWidget(self.limit_state)
        layout.addWidget(self.limits_group)

        self.review_group = QGroupBox("3  Review")
        review_layout = QVBoxLayout(self.review_group)
        self.review_summary = QPlainTextEdit(
            "The worker will verify the exact Agentic account and return the account, symbols, "
            "time window, risk limits, allocation policy, and earnings thresholds."
        )
        self.review_summary.setReadOnly(True)
        self.review_summary.setAccessibleName("Complete reviewed session")
        self.review_summary.setMinimumHeight(92)
        self.review_summary.setMaximumHeight(260)
        self.review_button = QPushButton("Review exact session")
        self.review_button.clicked.connect(lambda: self._spawn(self._review()))
        review_layout.addWidget(self.review_summary)
        review_layout.addWidget(self.review_button)
        layout.addWidget(self.review_group)

        self.start_group = QGroupBox("4  Start")
        start_layout = QVBoxLayout(self.start_group)
        self.start_summary = QLabel(
            "Start stays locked until the worker has reviewed this exact candidate. Final approval requires the displayed exact-scope phrase."
        )
        self.start_summary.setObjectName("settingsDescription")
        self.start_summary.setWordWrap(True)
        self.start_button = QPushButton("Review first")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self._prompt_start)
        start_layout.addWidget(self.start_summary)
        start_layout.addWidget(self.start_button)
        layout.addWidget(self.start_group)

        self.research_disclosure = QPushButton("Optional research & paper  ▸")
        self.research_disclosure.setCheckable(True)
        self.research_disclosure.setToolTip(
            "Show controls for the worker-hosted research-only MCP bridge"
        )
        self.research_disclosure.toggled.connect(self._toggle_research_panel)
        layout.addWidget(self.research_disclosure)
        self.research_panel = QFrame()
        self.research_panel.setObjectName("card")
        research_layout = QVBoxLayout(self.research_panel)
        research_layout.setContentsMargins(12, 10, 12, 12)
        research_note = QLabel(
            "Optional research bridge hosted by the same worker and broker session. It can read research "
            "context, but order tools are structurally unavailable. Stop, Revoke, and Exit disable it."
        )
        research_note.setObjectName("settingsDescription")
        research_note.setWordWrap(True)
        self.research_status = QLabel("Off · orders unavailable")
        self.research_status.setWordWrap(True)
        self.research_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.research_button = QPushButton("Enable research MCP")
        self.research_button.clicked.connect(lambda: self._spawn(self._toggle_research()))
        research_layout.addWidget(research_note)
        research_layout.addWidget(self.research_status)
        research_layout.addWidget(self.research_button)

        paper_note = QLabel(
            "Virtual paper only · no broker orders. Choose the amounts yourself; current quotes require a reviewed account."
        )
        paper_note.setObjectName("settingsDescription")
        paper_note.setWordWrap(True)
        research_layout.addWidget(paper_note)
        paper_form = QFormLayout()
        self.paper_source = QComboBox()
        self.paper_source.addItem("Synthetic demo", "demo")
        self.paper_source.addItem("Current broker quotes", "broker_quotes")
        self.paper_initial_cash = QLineEdit()
        self.paper_initial_cash.setPlaceholderText("Virtual starting cash")
        self.paper_trade_cash = QLineEdit()
        self.paper_trade_cash.setPlaceholderText("Virtual cash per entry")
        self.paper_loop_demo = QCheckBox("Repeat the synthetic demo until stopped")
        self.paper_news = QCheckBox("Include public news headlines")
        self.paper_social = QCheckBox("Include unverified public Bluesky posts")
        self.paper_social.setEnabled(False)
        self.paper_local_ai = QCheckBox("Use a local Ollama model for advisory context")
        self.paper_ai_model = QLineEdit()
        self.paper_ai_model.setPlaceholderText("Installed local model name")
        self.paper_ai_model.setEnabled(False)
        paper_form.addRow("Price source", self.paper_source)
        paper_form.addRow("Starting amount", self.paper_initial_cash)
        paper_form.addRow("Amount per entry", self.paper_trade_cash)
        paper_form.addRow("Demo", self.paper_loop_demo)
        paper_form.addRow("Research", self.paper_news)
        paper_form.addRow("Social", self.paper_social)
        paper_form.addRow("Local AI", self.paper_local_ai)
        paper_form.addRow("Ollama model", self.paper_ai_model)
        research_layout.addLayout(paper_form)
        paper_buttons = QHBoxLayout()
        self.paper_start_button = QPushButton("Start virtual paper")
        self.paper_start_button.clicked.connect(lambda: self._spawn(self._start_paper()))
        self.paper_stop_button = QPushButton("Stop paper")
        self.paper_stop_button.clicked.connect(lambda: self._spawn(self._stop_paper()))
        self.paper_stop_button.setEnabled(False)
        paper_buttons.addWidget(self.paper_start_button)
        paper_buttons.addWidget(self.paper_stop_button)
        research_layout.addLayout(paper_buttons)
        self.paper_initial_cash.textChanged.connect(self._refresh_controls)
        self.paper_trade_cash.textChanged.connect(self._refresh_controls)
        self.paper_source.currentIndexChanged.connect(self._refresh_controls)
        self.paper_source.currentIndexChanged.connect(self._update_paper_options)
        self.paper_loop_demo.toggled.connect(self._refresh_controls)
        self.paper_news.toggled.connect(self.paper_social.setEnabled)
        self.paper_news.toggled.connect(
            lambda enabled: self.paper_social.setChecked(False) if not enabled else None
        )
        self.paper_local_ai.toggled.connect(self.paper_ai_model.setEnabled)
        self.paper_local_ai.toggled.connect(self._refresh_controls)
        self.paper_ai_model.textChanged.connect(self._refresh_controls)
        self._update_paper_options()
        self.research_panel.setVisible(False)
        layout.addWidget(self.research_panel)
        layout.addStretch()
        return panel

    def _toggle_research_panel(self, expanded: bool) -> None:
        self.research_panel.setVisible(expanded)
        self.research_disclosure.setText(
            "Optional research & paper  ▾" if expanded else "Optional research & paper  ▸"
        )

    def _update_paper_options(self) -> None:
        current_quotes = self.paper_source.currentData() == "broker_quotes"
        self.paper_news.setEnabled(current_quotes)
        if not current_quotes:
            self.paper_news.setChecked(False)
            self.paper_social.setChecked(False)
            self.paper_local_ai.setChecked(False)
        self.paper_social.setEnabled(current_quotes and self.paper_news.isChecked())
        self.paper_local_ai.setEnabled(current_quotes)
        self.paper_ai_model.setEnabled(current_quotes and self.paper_local_ai.isChecked())
        self._refresh_controls()

    @staticmethod
    def _path_row(line_edit: QLineEdit, callback: Callable[[], None]) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        button = QPushButton("Choose…")
        button.clicked.connect(callback)
        layout.addWidget(line_edit, 1)
        layout.addWidget(button)
        return row

    def _build_activity_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("card")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 12, 14, 14)
        title = QLabel("Activity")
        title.setObjectName("dialogTitle")
        note = QLabel("Local control events only. Broker confirmations remain authoritative.")
        note.setObjectName("settingsDescription")
        note.setWordWrap(True)
        self.activity = QListWidget()
        self.activity.setAccessibleName("Session activity")
        self.activity.setAlternatingRowColors(True)
        layout.addWidget(title)
        layout.addWidget(note)
        layout.addWidget(self.activity, 1)
        self._log("info", "Ready. Connect to begin; no worker was started by opening this window.")
        return panel
