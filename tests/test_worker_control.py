"""Durable worker intent, including stop requests from another process."""

from pathlib import Path

from grande_alpha.execution.worker_control import WorkerControlStore, WorkerPermission


def test_stop_from_another_connection_survives_restart(tmp_path: Path) -> None:
    database = tmp_path / "execution.db"
    owner = WorkerControlStore(database)
    operator = WorkerControlStore(database)
    try:
        owner.review(
            account="agentic-123",
            scope_digest="approved-scope",
            candidate_path="candidate.json",
            authorization_path="authorization.json",
            earnings_database="earnings.db",
            poll_seconds=5.0,
        )
        owner.start("approved-scope")
        before = owner.current()
        assert before.running is True
        assert owner.may_submit(before.generation, "approved-scope") is True

        operator.stop()
        assert owner.may_submit(before.generation, "approved-scope") is False
        assert owner.current().running is False
    finally:
        owner.close()
        operator.close()

    recovered = WorkerControlStore(database)
    try:
        assert recovered.current().running is False
        assert recovered.current().scope_digest == "approved-scope"
    finally:
        recovered.close()


def test_changed_scope_cannot_start_reviewed_worker(tmp_path: Path) -> None:
    control = WorkerControlStore(tmp_path / "execution.db")
    try:
        control.review(
            account="agentic-123",
            scope_digest="approved-scope",
            candidate_path="candidate.json",
            authorization_path="authorization.json",
            earnings_database="earnings.db",
            poll_seconds=5.0,
        )
        try:
            control.start("changed-scope")
        except RuntimeError as exc:
            assert "scope" in str(exc).lower()
        else:
            raise AssertionError("A changed scope started without review")
    finally:
        control.close()


def test_worker_permission_requires_both_saved_intent_and_permit(tmp_path: Path) -> None:
    control = WorkerControlStore(tmp_path / "execution.db")
    try:
        control.review(
            account="agentic-123", scope_digest="approved-scope",
            candidate_path="candidate.json", authorization_path="authorization.json",
            earnings_database="earnings.db", poll_seconds=5.0,
        )
        state = control.start("approved-scope")
        gate = WorkerPermission(control, state.generation, lambda _digest: True)
        assert gate("approved-scope") is True
        control.stop()
        try:
            gate("approved-scope")
        except RuntimeError as exc:
            assert "stopped" in str(exc).lower()
        else:
            raise AssertionError("Stopped worker still had order permission")
        blocked = WorkerPermission(control, state.generation, lambda _digest: False)
        try:
            blocked("approved-scope")
        except RuntimeError:
            pass
        else:
            raise AssertionError("Denied user permit was ignored")
    finally:
        control.close()
