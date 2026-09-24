"""A user-started worker answers local controls without opening the broker."""

import asyncio
from pathlib import Path

import pytest

from grande_alpha.execution.worker_process import launch_worker


@pytest.mark.asyncio
async def test_launch_status_and_shutdown_on_disposable_data_dir(tmp_path: Path) -> None:
    client = await asyncio.to_thread(launch_worker, tmp_path, timeout_seconds=10)
    status = await asyncio.to_thread(client.request, "status", {})
    assert status["running"] is False
    assert status["connected"] is False
    result = await asyncio.to_thread(client.request, "shutdown", {})
    assert result["process_exiting"] is True
