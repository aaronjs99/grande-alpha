"""Worker-state projection, control availability, and desktop activity display."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QListWidgetItem

from grande_alpha.execution.worker_status import disconnected_worker_status
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
        epoch = self._status_epoch
        self._refresh_controls()
        try:
            status = await self._request("status", {}, timeout_seconds=3)
            if epoch != self._status_epoch:
                return
            self._worker_seen = True
            self._apply_status(status)
        except Exception as error:
            if epoch != self._status_epoch:
                return
            status = await asyncio.to_thread(disconnected_worker_status, self.data_dir)
            if epoch != self._status_epoch:
                return
            self._apply_disconnected_status(status)
            if show_errors:
                self._show_error(f"Worker status is unavailable: {error}")
        finally:
            self._status_in_flight = False
            self._refresh_controls()

    def _apply_disconnected_status(self, status: dict[str, Any]) -> None:
        was_reachable = self._status.get("worker_reachable") is True
        self._status_epoch += 1
        self._status = status
        self._invalidate_review("Worker status is unverified; review again after reconnecting")
        self.worker_card.value.setText("OFFLINE")
        phase = str(status["phase"])
        self.session_card.value.setText("Unknown" if phase.startswith("Unknown") else phase)
        self.session_card.value.setToolTip(phase)
        self.session_card.value.setAccessibleDescription(phase)
        self.account_card.value.setText("Unverified")
        self.coverage_card.value.setText("Unverified")
        self.coverage_card.setToolTip("The worker is unreachable; previous data coverage may be stale.")
        age = self._age_text(self._last_status_at)
        previous = f"Last worker response {age}. " if age is not None else ""
        guidance = previous + status["guidance"]
        self.status_notice.setText("Worker unavailable · " + (
            "local stop saved" if status.get("local_stop_recorded") is True else "trading unverified"
        ))
        self.status_notice.setToolTip(guidance)
        self.recovery_detail.setText(guidance)
        self.connect_detail.setText(status["guidance"])
        self._render_research_status(None)
        self._update_monitor(self._last_worker_status, str(self._last_worker_status.get("phase", "")))
        self.monitor_summary.setText(previous + status["guidance"])
        self.monitor_cycle.setText("Last reported: " + self.monitor_cycle.text())
        self.monitor_cycle_time.setText("Unverified — " + self.monitor_cycle_time.text())
        self.monitor_data.setText("Last reported: " + self.monitor_data.text())
        self._apply_responsive_layout(self.width(), self.height(), force=True)
        if was_reachable:
            self._log("warning", "Worker is unreachable; trading and broker state are unverified.")
        self._refresh_controls()

    def _apply_status(self, status: dict[str, Any]) -> None:
        current_generation = self._status.get("generation")
        incoming_generation = status.get("generation")
        if (type(current_generation) is int and type(incoming_generation) is int
                and incoming_generation < current_generation):
            raise RuntimeError("Worker response predates the current local control record; refresh status")
        was_offline = self._status.get("worker_reachable") is False and self._last_status_at is not None
        was_running = self._status.get("running") is True
        previous_phase = self._status.get("phase")
        previous_error = self._status.get("error")
        self._status_epoch += 1
        self._status = {**status, "worker_reachable": True, "status_source": "worker"}
        self._last_worker_status = dict(status)
        self._last_status_at = datetime.now(UTC).isoformat()
        self.status_notice.setToolTip("Worker status received just now. Broker confirmations remain authoritative.")
        self.recovery_detail.setText("Worker status received just now. Verify orders and holdings with the broker.")
        if self._review_result is not None:
            reviewed_scope = self._review_result.get("scope_digest")
            worker_scope = status.get("scope_digest")
            if (reviewed_scope != worker_scope
                    or self._review_result.get("generation") != incoming_generation):
                self._invalidate_review("Worker scope or control generation changed; review again")
        running = status.get("running") is True
        connected = status.get("connected") is True
        phase = str(status.get("phase") or ("Running" if running else "Stopped"))
        self.worker_card.value.setText("RUNNING" if running else "READY")
        self.session_card.value.setText(phase)
        self.session_card.value.setToolTip(phase)
        self.session_card.value.setAccessibleDescription(phase)
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
        else:
            self.connect_detail.setText("Worker reachable; no broker connection was reported.")
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
        elif was_offline:
            self._apply_responsive_layout(self.width(), self.height(), force=True)
        if was_offline:
            self._log("info", "Worker status is available again. Review the session before another Start.")
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
        if self._limits_dirty or self._candidate_document is None or not self._candidate_ready:
            return False
        candidate = Path(self.candidate_edit.text().strip()) if self.candidate_edit.text().strip() else None
        return bool(
            self._status.get("connected") is True
            and self._status.get("worker_reachable") is True
            and not self._status.get("running")
            and candidate is not None
            and candidate.is_file()
            and self.authorization_edit.text().strip()
            and self.earnings_edit.text().strip()
        )

    def _can_connect(self) -> bool:
        if self._status.get("worker_reachable") is False:
            return self._status.get("control_record") == "missing" or self._status.get("local_stop_recorded") is True
        return self._status.get("running") is not True

    def _monitor_required(self) -> bool:
        if self._status.get("running") is True:
            return True
        return self._status.get("worker_reachable") is False and (
            self._status.get("recorded_running") is True
            or self._status.get("control_record") == "unavailable"
            or (self._last_worker_status.get("running") is True
                and self._status.get("local_stop_recorded") is not True)
        )

    def _refresh_controls(self) -> None:
        connected = self._status.get("connected") is True
        running = self._status.get("running") is True
        reviewed_scope = self._review_result.get("scope_digest") if self._review_result else ""
        transition_busy = self._busy or self._stop_in_flight or self._exit_in_flight or self._paper_busy
        can_connect = not transition_busy and self._can_connect()
        self.connect_button.setEnabled(can_connect)
        if getattr(self, "_connect_action", None) is not None:
            self._connect_action.setEnabled(can_connect)
        self.refresh_status_button.setEnabled(not self._status_in_flight and not self._exit_in_flight)
        self.connect_button.setText("Connected" if connected else "Connect Robinhood")
        can_prepare = not running and not transition_busy
        self.limits_group.setEnabled(can_prepare)
        self.new_candidate_button.setEnabled(can_prepare)
        self.edit_candidate_button.setEnabled(can_prepare and self._candidate_document is not None)
        self.save_limits_button.setEnabled(
            can_prepare and self._candidate_ready
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
        self.paper_start_button.setEnabled(self._can_start_paper())
        self.paper_stop_button.setEnabled(paper_active and not self._paper_busy and not transition_busy)
        self.desk_start_button.setEnabled(not paper_active and not transition_busy)
        self.desk_stop_button.setEnabled(self.paper_stop_button.isEnabled())
        # Stop remains visible and actionable even when status probing failed. It never starts a worker.
        self.stop_button.setEnabled(not self._stop_in_flight and not self._exit_in_flight)

    def _can_start_paper(self) -> bool:
        research = self._status.get("research") or {}
        paper = research.get("paper") or {}
        broker_quotes = self.paper_source.currentData() == "broker_quotes"
        account_ready = self._status.get("connected") is True and bool(self._status.get("account"))
        return bool(
            (account_ready if broker_quotes else True) and not self._status.get("running")
            and self._can_connect() and not paper.get("active")
            and not (self._busy or self._stop_in_flight or self._exit_in_flight or self._paper_busy)
            and self.paper_initial_cash.text().strip() and self.paper_trade_cash.text().strip()
            and (not self.paper_local_ai.isChecked() or self.paper_ai_model.text().strip())
        )

    def _render_research_status(self, research: Any) -> None:
        reachable = self._status.get("worker_reachable") is True
        self.desk.render(research if reachable else self._last_worker_status.get("research"), reachable=reachable)
        if reachable:
            report = research if isinstance(research, dict) else {}
            paper = report.get("paper") or {}
            mode = "Paper" if paper.get("active") is True else "Research"
            self.status_notice.setText(
                f"● Trading · {self._status.get('phase') or 'Running'}" if self._status.get("running") is True else
                f"● {mode} · {report.get('phase') or 'Running'}" if report.get("running") is True else
                "○ Connected · trading stopped" if self._status.get("connected") is True else
                "○ Worker ready · broker not connected"
            )
        if self._status.get("worker_reachable") is False:
            self.research_status.setText("Research and paper status unverified · worker unreachable")
            self.research_button.setText("Enable research MCP")
            return
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
                f"{paper.get('position_count', 0)} virtual holdings"
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
        self.desk.record_event({"error": "WARN", "warning": "WARN", "success": "BOOK"}.get(level, "INFO"), message)

    def _show_error(self, message: str) -> None:
        self.banner.setText(message)
        self.banner.setVisible(True)
        self._log("error", message)

    def _clear_banner(self) -> None:
        self.banner.clear()
        self.banner.setVisible(False)
