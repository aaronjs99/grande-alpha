import pytest

import grande_alpha.read_retry as retries
from grande_alpha.broker.base import BrokerError


@pytest.mark.asyncio
async def test_transient_read_retries_are_bounded(monkeypatch):
    waits = []

    async def no_wait(seconds):
        waits.append(seconds)

    monkeypatch.setattr(retries.asyncio, "sleep", no_wait)
    calls = 0

    async def read():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise TimeoutError("temporary read timeout")
        return "complete"

    assert await retries.read_with_backoff(read) == "complete"
    assert calls == 3
    assert waits == [.25, .5]


@pytest.mark.asyncio
async def test_nontransport_failure_is_not_retried():
    calls = 0

    async def read():
        nonlocal calls
        calls += 1
        raise ValueError("invalid broker response")

    with pytest.raises(ValueError, match="invalid"):
        await retries.read_with_backoff(read)
    assert calls == 1


@pytest.mark.asyncio
async def test_broker_read_timeout_retries_but_schema_error_does_not(monkeypatch):
    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(retries.asyncio, "sleep", no_wait)
    calls = 0

    async def timed_out_read():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise BrokerError("Robinhood get_accounts timed out after 10s")
        return "recovered"

    assert await retries.read_with_backoff(timed_out_read) == "recovered"
    assert calls == 2

    async def malformed_read():
        nonlocal calls
        calls += 1
        raise BrokerError("Unexpected response from get_accounts")

    with pytest.raises(BrokerError, match="Unexpected"):
        await retries.read_with_backoff(malformed_read)
    assert calls == 3
