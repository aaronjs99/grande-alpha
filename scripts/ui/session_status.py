"""Worker-state projection, control availability, and desktop activity display."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QListWidgetItem

from grande_alpha.ui.session_components import display_value


class SessionStatus:
    def _schedule_status(self) -> None:
        if self._status_in_flight or self._closing:
            return
        self._spawn(self._refresh_status(show_errors=False))

    async def _refresh_status(self, *, show_errors: bool) -> None:
        if self._status_in_flight:
            return
        self._status_in_flight = True
        try:
            status = await self._request("status", {}, timeout_seconds=3)
            self._worker_seen = True
            self._apply_status(status)
        except Exception as error:
            previous = self._status
            self._status = {}
            self.worker_card.value.setText("OFFLINE")
            self.session_card.value.setText("Stopped")
            if previous:
                self._log("warning", "Local worker is no longer reachable.")
            if show_errors:
                self._show_error(f"Worker status is unavailable: {error}")
            self._refresh_controls()
        finally:
            self._status_in_flight = False

    def _apply_status(self, status: dict[str, Any]) -> None:
        was_running = self._status.get("running") is True
        previous_phase = self._status.get("phase")
        previous_error = self._status.get("error")
        self._status = status
        if self._review_result is not None:
            reviewed_scope = self._review_result.get("scope_digest")
            worker_scope = status.get("scope_digest")
            if worker_scope and reviewed_scope != worker_scope:
                self._invalidate_review("Worker scope changed in another client")
        running = status.get("running") is True
        connected = status.get("connected") is True
        phase = str(status.get("phase") or ("Running" if running else "Stopped"))
        self.worker_card.value.setText("RUNNING" if running else "READY")
        self.session_card.value.setText(phase)
        account = status.get("account_masked") or ""
        if account:
            self.account_card.value.setText(str(account))
        elif connected:
            self.account_card.value.setText("Connected")
        else:
            self.account_card.value.setText("Not connected")
        if connected:
            self.connect_detail.setText(
                f"Worker connected{f' to {account}' if account else ''}."
            )
        error = str(status.get("error") or "")
        self._render_research_status(status.get("research"))
        coverage = status.get("data_coverage")
        if isinstance(coverage, dict) and coverage:
            market = "market ready" if coverage.get("market_history_ready") is True else "market warming"
            events = coverage.get("stock_events", 0)
            missing = coverage.get("missing")
            missing_count = len(missing) if isinstance(missing, list) else 0
            self.coverage_card.value.setText(
                f"{market} · {events} events · {missing_count} missing"
            )
            self.coverage_card.setToolTip(display_value(coverage))
        else:
            self.coverage_card.value.setText("Awaiting first cycle")
            self.coverage_card.setToolTip("The worker has not reported a completed data snapshot.")
        self._update_monitor(status, phase)
        if running != was_running:
            self._apply_responsive_layout(self.width(), self.height(), force=True)
        if phase != previous_phase and previous_phase is not None:
            self._log("info", f"Worker phase: {phase}")
        if error and error != previous_error:
            self._show_error(f"Worker reported: {error}")
        self._refresh_controls()

    def _update_monitor(self, status: dict[str, Any], phase: str) -> None:
        running = status.get("running") is True
        if not running:
            self.monitor_summary.setText("Start a session to monitor it here.")
        elif phase == "Waiting for market":
            self.monitor_summary.setText(
                "Waiting for the supported market window. The worker stays active but does not "
                "request a strategy snapshot or submit orders while the market is closed."
            )
        else:
            self.monitor_summary.setText(
                "The worker checks current data against the approved allocation and limits. "
                "Use STOP TRADING to block further automatic submissions."
            )

        cycle = status.get("last_cycle")
        if isinstance(cycle, dict) and isinstance(cycle.get("status"), str):
            labels = {
                "NO_TICKET": "No portfolio change needed",
                "THESIS_NOT_VALID": "Entry skipped — research condition not met",
                "BELOW_MINIMUM": "No order — below the minimum size",
                "RISK_BLOCKED": "Risk controls blocked a new order",
                "RESPONSE_RECORDED": "Broker response recorded",
                "BUSY": "A strategy check is already in progress",
            }
            label = labels.get(cycle["status"], "Strategy check completed")
            self.monitor_cycle.setText(label)
            cycle_time = self._age_text(cycle.get("at"))
            self.monitor_cycle_time.setText(
                f"Last check {cycle_time}. "
                + (
                    "The broker response is not a fill confirmation."
                    if cycle.get("submitted") is True
                    else "No order submission was reported for this check."
                )
            )
        else:
            self.monitor_cycle.setText("No cycle reported yet")
            self.monitor_cycle_time.setText("")

        data_time = self._age_text(status.get("last_data_at"))
        self.monitor_data.setText(
            "No market snapshot reported yet"
            if data_time is None
            else f"Latest snapshot {data_time}"
        )

    @staticmethod
    def _age_text(value: Any) -> str | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if instant.tzinfo is None:
                return "time unavailable (no timezone)"
            seconds = (datetime.now().astimezone() - instant.astimezone()).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return "time unavailable"
        if seconds < -5:
            return "time unavailable (device clock differs)"
        seconds = max(0, seconds)
        if seconds < 60:
            return "just now" if seconds < 5 else f"{int(seconds)} seconds ago"
        if seconds < 3600:
            minutes = int(seconds // 60)
            return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours}h {minutes}m ago" if minutes else f"{hours}h ago"

    def _can_review(self) -> bool:
        if self._limits_dirty or self._candidate_document is None:
            return False
        candidate = Path(self.candidate_edit.text().strip()) if self.candidate_edit.text().strip() else None
        return bool(
            self._status.get("connected") is True
            and not self._status.get("running")
            and candidate is not None
            and candidate.is_file()
            and self.authorization_edit.text().strip()
            and self.earnings_edit.text().strip()
        )

    def _refresh_controls(self) -> None:
        connected = self._status.get("connected") is True
        running = self._status.get("running") is True
        reviewed_scope = self._review_result.get("scope_digest") if self._review_result else ""
        transition_busy = self._busy or self._stop_in_flight or self._exit_in_flight
        self.connect_button.setEnabled(not transition_busy and not running)
        self.connect_button.setText("Connected" if connected else "Connect Robinhood")
        self.limits_group.setEnabled(connected and not running and not transition_busy)
        self.save_limits_button.setEnabled(
            connected and not running and not transition_busy and self._candidate_document is not None
            and self._limits_dirty
        )
        self.review_button.setEnabled(not transition_busy and self._can_review())
        self.start_button.setEnabled(
            bool(reviewed_scope) and connected and not running and not transition_busy
        )
        self.start_button.setText("Approve and start" if reviewed_scope else "Review first")
        self.research_button.setEnabled(
            connected and bool(self._status.get("account")) and not transition_busy
            and not self._research_busy and not self._status.get("running")
        )
        research = self._status.get("research")
        paper = research.get("paper") if isinstance(research, dict) else None
        paper_active = isinstance(paper, dict) and paper.get("active") is True
        broker_quotes = self.paper_source.currentData() == "broker_quotes"
        account_ready = connected and bool(self._status.get("account"))
        can_start_paper = (
            (account_ready if broker_quotes else True) and not self._status.get("running")
            and not transition_busy and not self._paper_busy and not paper_active
            and bool(self.paper_initial_cash.text().strip()) and bool(self.paper_trade_cash.text().strip())
            and (not self.paper_local_ai.isChecked() or bool(self.paper_ai_model.text().strip()))
        )
        self.paper_start_button.setEnabled(can_start_paper)
        self.paper_stop_button.setEnabled(paper_active and not self._paper_busy and not transition_busy)
        # Stop remains visible and actionable even when status probing failed. It never starts a worker.
        self.stop_button.setEnabled(not self._stop_in_flight and not self._exit_in_flight)

    def _render_research_status(self, research: Any) -> None:
        if not isinstance(research, dict):
            self.research_status.setText("Research MCP off · broker orders unavailable")
            self.research_button.setText("Enable research MCP")
            return
        enabled = research.get("enabled") is True
        phase = research.get("phase") or ("Running" if research.get("running") else "Stopped")
        bridge = research.get("bridge_path") or ("MCP connection off" if not enabled else "Local bridge path pending")
        paper = research.get("paper")
        paper_status = "Paper session: not started"
        if isinstance(paper, dict):
            state = "running" if paper.get("active") else "saved"
            paper_status = (
                f"Paper {state} · {paper.get('source', 'unknown source')} · "
                f"virtual equity ${paper.get('equity', '—')} · "
                f"P&L ${paper.get('total_pnl', '—')} · "
                f"{len(paper.get('positions', []))} virtual holdings"
            )
        self.research_status.setText(
            f"Research: {phase} · broker orders unavailable\n{paper_status}\n{bridge}"
        )
        self.research_button.setText("Disable research MCP" if enabled else "Enable research MCP")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._refresh_controls()

    def _log(self, level: str, message: str) -> None:
        timestamp = datetime.now().strftime("%I:%M:%S %p")
        item = QListWidgetItem(f"{timestamp}  {message}")
        if level == "success":
            item.setForeground(QColor("#00c805"))
        elif level == "warning":
            item.setForeground(QColor("#f2c14e"))
        elif level == "error":
            item.setForeground(QColor("#ff697d"))
        self.activity.insertItem(0, item)
        while self.activity.count() > 200:
            self.activity.takeItem(self.activity.count() - 1)
        self.event_card.value.setText(message[:48] + ("…" if len(message) > 48 else ""))

    def _show_error(self, message: str) -> None:
        self.banner.setText(message)
        self.banner.setVisible(True)
        self._log("error", message)

    def _clear_banner(self) -> None:
        self.banner.clear()
        self.banner.setVisible(False)
