from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import tomllib

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_agent_mcp import desktop as desktop
from test_agent_mcp import pump

from grande_alpha import chatgpt_connection as setup
from grande_alpha.ui.chatgpt_setup import TEST_PROMPT, ChatGPTSetupDialog


def test_settings_preserve_existing_options_and_undo_later_unrelated_edits(tmp_path):
    path = tmp_path / 'config.toml'
    original = b'# Keep this comment\r\nmodel = "user-choice"\r\n[mcp_servers.other]\r\ncommand = "other-tool"\r\n'
    path.write_bytes(original)
    settings = setup.connection_settings(tmp_path / 'folder with spaces' / 'bridge.db')
    setup.add_connection(path, settings)
    added = path.read_bytes()
    assert added.startswith(original)
    parsed = tomllib.loads(added.decode())
    assert parsed['model'] == 'user-choice'
    assert parsed['mcp_servers']['other']['command'] == 'other-tool'
    assert parsed['mcp_servers']['grande-alpha'] == settings
    setup.add_connection(path, settings)
    assert path.read_bytes() == added  # repeated setup does not duplicate or overwrite
    backups = list(tmp_path.glob('config.toml.grande-backup-*'))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    if os.name != 'nt':
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600
    later = b'\n[mcp_servers.later]\ncommand = "new-user-tool"\n'
    path.write_bytes(added + later)
    setup.remove_connection(path, settings)
    assert path.read_bytes() == original + later


@pytest.mark.parametrize('original', [
    b'invalid = [',
    b'[mcp_servers.grande-alpha]\ncommand = "user-owned"\n',
    b'mcp_servers = {other = {command = "inline"}}\n',
    b'# BEGIN GRANDE research connection\n',
])
def test_setup_refuses_conflicts_without_touching_file(tmp_path, original):
    path = tmp_path / 'config.toml'
    path.write_bytes(original)
    with pytest.raises(setup.ConnectionSetupError):
        setup.add_connection(path, setup.connection_settings(tmp_path / 'bridge.db'))
    assert path.read_bytes() == original
    assert not list(tmp_path.glob('*backup*'))


def test_missing_file_quoted_paths_and_user_modified_entry(tmp_path):
    path = tmp_path / 'new' / 'config.toml'
    settings = {'command': 'C:\\Program Files\\Python\\python.exe',
                'args': ['-m', 'grande_alpha.agent_mcp', '--bridge', '/tmp/quote" and $ cash/bridge.db'],
                'env': {'PYTHONPATH': '/tmp/quote" and $ cash/src'}}
    setup.add_connection(path, settings)
    assert tomllib.loads(path.read_text())['mcp_servers']['grande-alpha'] == settings
    raw = path.read_bytes().replace(b'args =', b'enabled = false\nargs =')
    path.write_bytes(raw)
    with pytest.raises(setup.ConnectionSetupError):
        setup.remove_connection(path, settings)
    assert path.read_bytes() == raw


def test_symlink_and_concurrent_edits_are_not_overwritten(tmp_path, monkeypatch):
    path = tmp_path / 'config.toml'
    target = tmp_path / 'target.toml'
    target.write_bytes(b'model = "old"\n')
    path.symlink_to(target)
    settings = setup.connection_settings(tmp_path / 'bridge.db')
    with pytest.raises(setup.ConnectionSetupError):
        setup.add_connection(path, settings)
    assert target.read_bytes() == b'model = "old"\n'
    path.unlink()
    path.write_bytes(b'model = "old"\n')
    real_read = setup._read
    calls = 0

    def raced_read(file):
        nonlocal calls
        calls += 1
        if calls == 2:
            path.write_bytes(b'model = "edited"\n')
        return real_read(file)

    monkeypatch.setattr(setup, '_read', raced_read)
    with pytest.raises(setup.ConnectionSetupError):
        setup.add_connection(path, settings)
    assert path.read_bytes() == b'model = "edited"\n'
    assert not list(tmp_path.glob('.grande-settings-*'))


@pytest.mark.asyncio
async def test_wizard_saved_config_launches_real_stdio_and_stop_revokes(desktop, tmp_path, monkeypatch):
    app, controller = desktop
    # No test may modify the executing user's ChatGPT configuration.
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'chatgpt'))
    dialog = ChatGPTSetupDialog(controller)
    assert not dialog.settings_path.exists()
    assert not controller.agent_bridge.session
    assert not dialog.next.isEnabled()
    dialog.add.click()
    assert dialog.saved
    assert not controller.agent_bridge.session
    dialog.next.click()
    dialog.allow.setChecked(True)
    assert controller.agent_bridge.session
    dialog.next.click()
    assert dialog.pages.currentIndex() == 2
    dialog.copy_prompt.click()
    assert app.clipboard().text() == TEST_PROMPT
    entry = tomllib.loads(dialog.settings_path.read_text())['mcp_servers']['grande-alpha']
    # Dependencies are injected in this CI runtime, unlike an installed user venv.
    env = dict(entry['env'])
    env['PYTHONPATH'] += os.pathsep + os.environ.get('PYTHONPATH', '')
    params = StdioServerParameters(command=entry['command'], args=entry['args'], env=env)
    task = asyncio.create_task(pump(controller))
    try:
        async with stdio_client(params) as (read, write), ClientSession(read, write) as client:
            await client.initialize()
            result = await client.call_tool('get_research_context')
            assert not result.isError
            assert json.loads(result.content[0].text)['mode'] == 'research_only'
            assert 'request reached' in dialog.request_status.text()
            assert not controller.agent.snapshot.running
            assert controller.risk.grant is None
            controller._stop_for_cancel('User STOP')
            assert not dialog.allow.isChecked()
            assert 'OFF' in dialog.access_status.text()
            assert (await client.call_tool('get_research_context')).isError
        dialog.remove.click()
        assert 'grande-alpha' not in tomllib.loads(dialog.settings_path.read_text()).get('mcp_servers', {})
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_frozen_help_does_not_offer_invalid_connection(desktop, tmp_path, monkeypatch):
    app, controller = desktop
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'chatgpt'))
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    dialog = ChatGPTSetupDialog(controller)
    assert not dialog.add.isEnabled()
    assert not dialog.copy_command.isEnabled()
    assert not dialog.settings_path.exists()
    assert not controller.agent_bridge.session
    dialog.close()
    dialog.deleteLater()
    app.processEvents()
