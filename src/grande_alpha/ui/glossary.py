from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QTableWidget,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

TERM_HELP: dict[str, str] = {
    "Account value": "The broker-reported total value of the selected Agentic account.",
    "Account reconciliation": (
        "How often GRANDE Alpha refreshes authoritative account, position, and order state from the broker."
    ),
    "Agentic account": (
        "The dedicated Robinhood account selected for GRANDE Alpha. The app does not select the regular "
        "investing account for its portfolio, position, or order views."
    ),
    "Authorized order type": "The only order type allowed during this bounded live session.",
    "Authorized session": "The only market-hours session allowed during this bounded live session.",
    "Authorized time in force": "How long submitted limit orders may remain active during this session.",
    "Base spread": "The fixed bid-ask spread charged to every modeled sandbox fill, measured in basis points.",
    "Breakout buffer": "Extra distance beyond an opening range required before the strategy treats it as a breakout.",
    "Broker fees": "Explicit brokerage or execution fees deducted from a personal research-fund calculation.",
    "Buying power": "Broker-reported cash or purchasing capacity currently available to the selected account.",
    "Calendar lookback": (
        "Calendar days requested from the data source. Weekends and holidays mean fewer market sessions."
    ),
    "Close momentum": "Minimum first-period price movement required by the closing-momentum research rule.",
    "Commission / order": "Fixed modeled fee applied to each virtual order in the sandbox.",
    "Completed analysis bar": (
        "Length of each completed causal price bar used to update the signal. It must match qualifying evidence data."
    ),
    "Contribution rate": "Percentage of eligible personal profit reserved for the optional planning ledger.",
    "Daily loss pause": "Session loss threshold that prevents new virtual entries after it is reached.",
    "Eligible contribution": (
        "Calculated planning amount after fees and tax reserve. GRANDE Alpha never transfers this money."
    ),
    "End handling": "Whether the sandbox forces a modeled closing fill on the final available bar of each session.",
    "Ensemble votes": "Minimum number of component research signals that must agree before taking exposure.",
    "Extra latency": "Additional completed bars between a virtual decision and its modeled execution.",
    "Fast EMA": "Shorter exponential moving average used by the momentum signal.",
    "Fill fraction": "Maximum percentage of a requested virtual quantity assumed to fill.",
    "Hard stop": "Adverse percentage move from entry that triggers a modeled exit decision.",
    "Imported bar interval": "Duration represented by each imported candle; it must match the intended analysis cadence.",
    "Integrity": (
        "Dataset coverage, missing and duplicate intervals, zero-volume bars, and reproducibility hash."
    ),
    "Limit offset": (
        "Maximum price concession from the current ask for a buy or bid for a sell, measured in basis points."
    ),
    "Live authority": "Whether a separately confirmed, time-limited real-order session is currently armed.",
    "Live shadow": "Live broker observations with virtual fills only; it cannot submit a real order.",
    "Local credential": "The Windows-stored Robinhood OAuth credential used to reconnect GRANDE Alpha.",
    "Long-history CSV": (
        "A user-supplied aligned QQQ/TQQQ/SQQQ file. Its license, accuracy, timezone, and completeness remain "
        "the user's responsibility."
    ),
    "Marketable-limit offset": (
        "Maximum price concession used to construct automatic whole-share limit orders from the current bid or ask."
    ),
    "Max order notional": "Maximum dollars that any single order may expose during the authorized session.",
    "Max daily notional": (
        "Maximum gross dollars across all submitted buys and sells during the session, including reserved "
        "authorizations awaiting submission or release."
    ),
    "Max orders per minute": "Hard ceiling on submitted order requests within any rolling minute.",
    "Max session loss": "Loss threshold that blocks new entries for the remainder of the authorized session.",
    "Max submitted orders": "Maximum number of order submissions allowed before the live grant locks.",
    "Max total exposure": "Maximum combined market value GRANDE Alpha may hold during the authorized session.",
    "Max volume participation": "Maximum share of a candle's reported volume available to a modeled fill.",
    "Settlement model": (
        "Controls whether virtual sale proceeds may be reused immediately or remain unsettled until "
        "the next observed trading session. Cash-account evidence should use the T+1 model."
    ),
    "Unsettled cash": (
        "Virtual equity from completed sales that still counts toward account value but cannot fund "
        "another cash-account purchase until the next modeled trading session."
    ),
    "Maximum entries": "Maximum number of new virtual positions the strategy may open per trading day.",
    "Maximum exposure": "Maximum percentage of sandbox equity that may be allocated to a position.",
    "Maximum hold": "Longest time a position may remain open before a modeled time exit.",
    "Maximum spread": "Widest bid-ask spread accepted before the live risk engine blocks a submission.",
    "Momentum horizon": "Number of completed bars used to measure recent directional price movement.",
    "Notes": "Optional context saved with this local planning entry.",
    "Opening range": "Initial minutes of a session used to define the opening high and low.",
    "Order cap": "Maximum dollars allocated to a single virtual sandbox entry.",
    "Order type": (
        "Market orders prioritize execution but not price. Limit orders constrain price but may partially fill or not fill."
    ),
    "Pair action (T,S)": (
        "Current command for TQQQ and SQQQ: -1 sell, 0 hold, or +1 buy. Inventory and risk masks can block a command."
    ),
    "Pause after losses": "Number of consecutive losing exits that pauses new virtual entries.",
    "Period (YYYY-MM)": "Month assigned to an optional personal research-fund planning entry.",
    "Preset": "A named starting configuration. Applying it replaces the visible sandbox parameters.",
    "Provenance manifest": (
        "Sidecar metadata declaring a dataset's source, license, timezone, session, interval, and "
        "construction assumptions so evidence can be reproduced and eligibility checked."
    ),
    "QQQ regime": "Current causal signal classification derived from completed QQQ observations.",
    "Quote request target": (
        "Desired delay between quote requests. Actual speed cannot exceed broker and network response time."
    ),
    "Quote, bar, and signal history": "How long local non-credential market observations are retained on this computer.",
    "Realized profit": "Closed-position profit before the separately entered fees and tax reserve.",
    "Rejection probability": "Chance that a virtual sandbox order is modeled as rejected.",
    "Research strategy": "The signal hypothesis evaluated by the sandbox; it is not a recommendation or guaranteed edge.",
    "Selected runtime policy": (
        "The signal policy used by normal and scheduled live-shadow runtime. CASH / hold is the fail-safe "
        "default and requests no leveraged position."
    ),
    "Risk budget": "Maximum percentage of sandbox equity risked using entry-to-stop distance.",
    "Run note": "Hypothesis or experiment note saved with the immutable run receipt.",
    "Saved runs": "Previously recorded sandbox configurations that can be loaded for reproduction.",
    "Session drawdown": "Loss from the live session's peak portfolio value, not the account's all-time drawdown.",
    "Session duration": "How long the bounded live grant remains valid before it automatically expires.",
    "Skip after open": "Minutes after the selected session opens during which new entries are blocked.",
    "Skip before close": "Minutes before the selected session closes during which new entries are blocked.",
    "Slippage / side": "Additional adverse execution cost applied separately to each modeled buy and sell.",
    "Slow EMA": "Longer exponential moving average used as the momentum signal's baseline.",
    "Source": "Where the replay bars come from. Synthetic scenarios can test software but cannot certify live review.",
    "Spread": "Difference between the current ask and bid; wider spreads increase execution cost and uncertainty.",
    "Starting cash": "Initial virtual cash for the sandbox replay; it does not move brokerage funds.",
    "Take-profit": "Favorable percentage move from entry that triggers a modeled exit decision.",
    "Tax reserve": "User-entered amount held back for possible taxes; GRANDE Alpha does not calculate tax liability.",
    "Time in force": (
        "GFD expires at the selected session's end. GTC can remain active at the broker for up to 90 days."
    ),
    "Trade decision every": (
        "Number of completed analysis bars between pair-action decisions. A decision can still be hold/no action."
    ),
    "Trading session": "Regular, extended, or 24 Hour Market window that the replay or order is allowed to use.",
    "Trend long horizon": "Longest completed-bar window used by the multi-horizon trend hypothesis.",
    "Trend medium horizon": "Middle completed-bar window used by the multi-horizon trend hypothesis.",
    "Trend short horizon": "Shortest completed-bar window used by the multi-horizon trend hypothesis.",
    "Trend threshold": "Minimum signal separation required before momentum is treated as directional.",
    "Volatility spread": "Additional modeled spread proportional to each candle's high-low range.",
    "Volatility target": "Annualized volatility objective used to reduce position size when recent volatility rises.",
    "Warm-up": "Completed bars observed before the strategy is allowed to produce its first decision.",
    "After-cost quality": "Whether modeled profit factor and expectancy remain adequate after execution costs.",
    "Closed-trade sample": "Number of completed after-cost round trips supporting trade-quality estimates.",
    "Cost stress": "Replay result after multiplying modeled execution costs to test whether a small edge survives.",
    "Data breadth": "Number of complete market sessions available for evidence; promotion requires at least 120.",
    "Data integrity": "Whether aligned bars are hash-valid, complete, nonduplicated, and free of missing expected intervals.",
    "Data recency": "Age of the final market observation; promotion requires no more than 30 days.",
    "Deflated Sharpe": "Probability that risk-adjusted performance remains meaningful after non-normal returns and all registered strategy trials are considered.",
    "Ending flat": "Whether the replay closes every virtual position and finishes in cash.",
    "Exact candidate identity": "Whether every training fold selected the exact configuration being considered for certification.",
    "Historical source": "Whether evidence comes from observed or imported market history rather than a synthetic scenario.",
    "Parameter stability": "Share of neighboring parameter configurations that remain profitable instead of relying on one tuned setting.",
    "Profit concentration": "Share of positive daily P/L contributed by the single best day; promotion caps it at 50%.",
    "Random-entry control": "Seeded chance-entry benchmark using comparable holding and sizing assumptions.",
    "Runtime sizing parity": (
        "Whether replay and shadow/live use the exact same certified position-sizing contract. "
        "This currently fails non-cash candidates because runtime does not share replay's "
        "risk-budget and volatility sizing."
    ),
    "Trading-session coverage": "Whether the evidence dataset covers the complete session selected by the strategy route.",
    "Trial-adjusted significance": "Statistical evidence after correcting for every candidate tried on the dataset.",
    "Walk-forward": "Purged chronological train/test folds that evaluate selections on later unseen sessions.",
}


TABLE_HEADER_HELP: dict[str, str] = {
    "Age": "Seconds since the timestamp of the latest quote received by GRANDE Alpha.",
    "Ask": "Lowest displayed price currently offered by a seller.",
    "Bid": "Highest displayed price currently offered by a buyer.",
    "Gate": "Independent condition that must pass before a live-review certificate can be created.",
    "Observed": "Value measured in this exact evidence run.",
    "Requirement": "Minimum rule the observed value must satisfy.",
    "Spread": "Difference between ask and bid, shown in basis points.",
    "Status": "PASS satisfies this gate; FAIL keeps the result shadow-only.",
    "Test": "Later chronological data used only for evaluation in this fold.",
    "Train": "Earlier chronological data used to choose a candidate in this fold.",
}


def term_help(term: str) -> str | None:
    return TERM_HELP.get(term)


def _tooltip(term: str, explanation: str) -> str:
    return f"<b>{escape(term)}</b><br>{escape(explanation)}"


class ExplainedLabel(QLabel):
    """A discoverable glossary term with mouse and assistive-technology help."""

    def __init__(
        self,
        term: str,
        explanation: str | None = None,
        parent: QWidget | None = None,
        *,
        compact: bool = False,
    ) -> None:
        explanation = explanation or TERM_HELP.get(term)
        if not explanation:
            raise KeyError(f"No glossary explanation registered for {term!r}")
        super().__init__(term, parent)
        self.term = term
        self.explanation = explanation
        self.setObjectName("explainedTerm")
        self.setToolTip(_tooltip(term, explanation))
        self.setToolTipDuration(30_000)
        self.setWhatsThis(explanation)
        self.setStatusTip(explanation)
        self.setAccessibleName(term)
        self.setAccessibleDescription(explanation)
        self.setCursor(Qt.CursorShape.WhatsThisCursor)
        compact_style = "color:#8fa4b8;font-size:9pt;" if compact else ""
        self.setStyleSheet(
            "QLabel {"
            f"{compact_style}border:0;border-bottom:1px dashed #6688a3;padding-bottom:1px;"
            "}"
            "QLabel:hover {color:#8fd3ff;border-bottom-color:#8fd3ff;}"
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            QToolTip.showText(event.globalPosition().toPoint(), self.toolTip(), self)
        super().mousePressEvent(event)


def add_explained_row(
    form: QFormLayout,
    term: str,
    field: QWidget | QLayout,
    explanation: str | None = None,
) -> ExplainedLabel:
    label = ExplainedLabel(term, explanation)
    form.addRow(label, field)
    return label


def apply_help(widget: QWidget, term: str, explanation: str | None = None) -> None:
    explanation = explanation or TERM_HELP.get(term)
    if not explanation:
        raise KeyError(f"No glossary explanation registered for {term!r}")
    widget.setToolTip(_tooltip(term, explanation))
    widget.setToolTipDuration(30_000)
    widget.setWhatsThis(explanation)
    widget.setAccessibleDescription(explanation)


def help_hint() -> QLabel:
    label = QLabel(
        "Tip: dashed-underlined terms explain themselves on hover or click. Press F1 for the glossary."
    )
    label.setObjectName("settingsDescription")
    label.setWordWrap(True)
    label.setAccessibleName("Glossary help tip")
    return label


def apply_table_header_help(table: QTableWidget) -> None:
    for column in range(table.columnCount()):
        item = table.horizontalHeaderItem(column)
        if item is None:
            continue
        explanation = TABLE_HEADER_HELP.get(item.text()) or TERM_HELP.get(item.text())
        if explanation:
            item.setToolTip(_tooltip(item.text(), explanation))


class GlossaryDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("GRANDE Alpha terminology and glossary")
        self.setMinimumSize(680, 520)
        layout = QVBoxLayout(self)
        title = QLabel("Terminology & glossary")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        intro = QLabel(
            "Search the same plain-language definitions used by dashed-underlined labels throughout the app."
        )
        intro.setObjectName("settingsDescription")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search a term or definition…")
        self.search.setAccessibleName("Search glossary")
        layout.addWidget(self.search)
        self.terms = QListWidget()
        self.terms.setAccessibleName("Glossary terms")
        for term in sorted(TERM_HELP, key=str.casefold):
            self.terms.addItem(term)
        layout.addWidget(self.terms, 2)
        self.definition = QLabel()
        self.definition.setWordWrap(True)
        self.definition.setObjectName("validationWarning")
        self.definition.setAccessibleName("Selected glossary definition")
        layout.addWidget(self.definition, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.search.textChanged.connect(self._filter)
        self.terms.currentTextChanged.connect(self._show_definition)
        if self.terms.count():
            self.terms.setCurrentRow(0)

    def _filter(self, query: str) -> None:
        needle = query.strip().casefold()
        first_visible = None
        for row in range(self.terms.count()):
            item = self.terms.item(row)
            haystack = f"{item.text()} {TERM_HELP[item.text()]}".casefold()
            hidden = bool(needle) and needle not in haystack
            item.setHidden(hidden)
            if not hidden and first_visible is None:
                first_visible = item
        current = self.terms.currentItem()
        if first_visible is not None and (current is None or current.isHidden()):
            self.terms.setCurrentItem(first_visible)
        elif first_visible is None:
            self.definition.setText("No glossary terms match that search.")

    def _show_definition(self, term: str) -> None:
        if term in TERM_HELP:
            self.definition.setText(f"{term}\n\n{TERM_HELP[term]}")
