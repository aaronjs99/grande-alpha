from datetime import UTC, datetime, time

import pytest

import grande_alpha.mixed_engine as mixed_engine


@pytest.mark.asyncio
async def test_source_read_failure_waits_without_revoking_or_submitting(monkeypatch):
    monkeypatch.setattr(mixed_engine, "utc_now", lambda: datetime(2026, 9, 23, 18, 0, tzinfo=UTC))
    monkeypatch.setattr(mixed_engine, "regular_session_times", lambda _date: (time(9, 30), time(16)))

    class Audit:
        def __init__(self):
            self.receipts = []

        def receipt(self, *args):
            self.receipts.append(args)

    class Engine:
        def __init__(self):
            self.audit = Audit()
            self.cycles = 0
            self.shutdowns = 0

        def _check(self):
            return None

        def heartbeat(self):
            return None

        async def cycle(self, _request, _theses):
            self.cycles += 1

        async def shutdown(self, **_kwargs):
            self.shutdowns += 1

    calls = 0

    async def source():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("temporary quote outage")
        return {"request": {}, "theses": {}}

    engine = Engine()
    assert await mixed_engine.run_cycles(engine, source, poll_seconds=.001, max_cycles=1) == 1
    assert calls == 2 and engine.cycles == 1 and engine.shutdowns == 1
    assert engine.audit.receipts[0][0] == "mixed_data_wait"
