from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_scheduled_launcher_is_auto_shadow_only_and_waits_for_single_instance() -> None:
    script = (ROOT / "scheduled-shadow.ps1").read_text(encoding="utf-8")

    assert "Get-GrandeAlphaPython $ProjectRoot -Windowed" in script
    assert "'grande_alpha.app', '--auto-shadow'" in script
    assert "PassThru = $true" in script
    assert "Wait = $true" in script
    assert "review_order" not in script
    assert "place_order" not in script
    assert "cancel_order" not in script


def test_schedule_manager_declares_safe_per_user_task_contract() -> None:
    script = (ROOT / "manage-shadow-schedule.ps1").read_text(encoding="utf-8")

    required = {
        "[Security.Principal.WindowsIdentity]::GetCurrent().Name",
        "LogonType = 'Interactive'",
        "RunLevel = 'Limited'",
        "StartWhenAvailable = $false",
        "WakeToRun = $true",
        "AllowStartIfOnBatteries = $true",
        "DontStopIfGoingOnBatteries = $true",
        "RunOnlyIfNetworkAvailable = $false",
        "MultipleInstances = 'IgnoreNew'",
        "DaysOfWeek = @('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')",
        "At = '06:20'",
        "Register-ScheduledTask @RegistrationParameters",
        "Unregister-ScheduledTask",
        "Test-FullyQualifiedPath",
    }
    for item in required:
        assert item in script
    assert "Password" not in script
    assert "--auto-shadow" in script


def test_local_installer_adds_setup_shortcut_without_registering_task() -> None:
    script = (ROOT / "install-local.ps1").read_text(encoding="utf-8")

    assert "Scheduled Shadow Setup.cmd" in script
    assert "GRANDE Alpha Shadow Schedule.lnk" in script
    assert "No task was enabled by this installer" in script
    assert "Register-ScheduledTask" not in script


def test_source_release_contains_schedule_management_and_launcher() -> None:
    script = (ROOT / "release.ps1").read_text(encoding="utf-8")

    assert "manage-shadow-schedule.ps1" in script
    assert "scheduled-shadow.ps1" in script
    assert "Scheduled Shadow Setup.cmd" in script


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Scheduled Tasks are Windows-only")
def test_definition_mode_is_read_only_and_reports_exact_task_contract() -> None:
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "manage-shadow-schedule.ps1"),
            "-Definition",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    definition = json.loads(result.stdout)

    assert definition["task_name"] == "GRANDE Alpha Live Shadow"
    assert definition["task_path"] == "\\"
    assert definition["local_time"] == "06:20"
    assert definition["days_of_week"] == [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
    ]
    assert definition["logon_type"] == "InteractiveToken"
    assert definition["run_level"] == "Limited"
    assert definition["stores_password"] is False
    assert definition["start_when_available"] is False
    assert definition["wake_to_run"] is True
    assert definition["allow_start_if_on_batteries"] is True
    assert definition["stop_if_going_on_batteries"] is False
    assert definition["network_required"] is False
    assert definition["multiple_instances"] == "IgnoreNew"
    assert definition["application_mode"] == "--auto-shadow"
    assert Path(definition["execute"]).is_absolute()
    assert Path(definition["working_directory"]) == ROOT
    assert str(ROOT / "scheduled-shadow.ps1") in definition["arguments"]
