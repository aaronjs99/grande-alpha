from __future__ import annotations

import sys

import pytest

from grande_alpha.windows_shortcut import (
    WINDOWS_APP_USER_MODEL_ID,
    set_shortcut_app_user_model_id,
    shortcut_app_user_model_id,
)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Shell integration")
def test_windows_shortcut_identity_round_trip(tmp_path) -> None:
    import win32com.client

    shortcut_path = tmp_path / "GRANDE Alpha.lnk"
    shortcut = win32com.client.Dispatch("WScript.Shell").CreateShortcut(str(shortcut_path))
    shortcut.TargetPath = sys.executable
    shortcut.Save()

    assert shortcut_app_user_model_id(shortcut_path) is None
    set_shortcut_app_user_model_id(shortcut_path)
    assert shortcut_app_user_model_id(shortcut_path) == WINDOWS_APP_USER_MODEL_ID
