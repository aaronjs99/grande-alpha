from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_scheduled_launcher_is_auto_shadow_only_and_waits_for_single_instance() -> None:
    script = (ROOT / "scheduled-shadow.ps1").read_text(encoding="utf-8")

    assert "Get-GrandeAlphaPython $ProjectRoot" in script
    assert "-m grande_alpha.app --auto-shadow" in script
    assert "& $PythonExe -m grande_alpha.app --auto-shadow" in script
    assert "Start-Process @" not in script
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
        "At = $LocalTriggerTime",
        "if ($null -eq $LocalTriggerTime)",
        "'Eastern Standard Time' = '09:20'",
        "'Central Standard Time' = '08:20'",
        "'Mountain Standard Time' = '07:20'",
        "'US Mountain Standard Time' = '06:20'",
        "'Pacific Standard Time' = '06:20'",
        "'US Mountain Standard Time'",
        "'Pacific Standard Time'",
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
    expected_times = {
        "Eastern Standard Time": "09:20",
        "Central Standard Time": "08:20",
        "Mountain Standard Time": "07:20",
        "US Mountain Standard Time": "06:20",
        "Pacific Standard Time": "06:20",
    }
    assert definition["local_time_by_windows_zone"] == expected_times
    host_time_zone_id = definition["host_time_zone_id"]
    if host_time_zone_id in expected_times:
        assert definition["local_time"] == expected_times[host_time_zone_id]
    else:
        # Definition mode is deliberately portable and read-only. Installation
        # remains fail-closed on unsupported host zones (including CI's UTC).
        assert definition["local_time"] is None
    assert definition["target_eastern_time_window"] == "07:00-09:20"
    assert definition["host_time_zone_id"]
    assert definition["supported_time_zone_ids"] == [
        "Eastern Standard Time",
        "Central Standard Time",
        "Mountain Standard Time",
        "US Mountain Standard Time",
        "Pacific Standard Time",
    ]
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
    assert definition["execution_time_limit_hours"] == 0
    assert definition["application_mode"] == "--auto-shadow"
    assert Path(definition["execute"]).is_absolute()
    assert Path(definition["working_directory"]) == ROOT
    assert str(ROOT / "scheduled-shadow.ps1") in definition["arguments"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Scheduled Tasks are Windows-only")
def test_contract_validator_rejects_tampered_task_object_without_scheduler_write() -> None:
    script = ROOT / "manage-shadow-schedule.ps1"
    command = rf"""
. '{script}'
if ($null -eq $Spec.local_time) {{
    # Exercise the pure contract validator on UTC CI hosts without weakening
    # the installation path's unsupported-zone rejection.
    $Spec.local_time = '09:20'
}}
$FakeAction = [pscustomobject]@{{
    Execute = $Spec.execute
    Arguments = $Spec.arguments
    WorkingDirectory = $Spec.working_directory
}}
$FakeTrigger = [pscustomobject]@{{
    StartBoundary = "2026-08-12T$($Spec.local_time):00"
    DaysOfWeek = 62
    WeeksInterval = 1
    Enabled = $true
}}
$FakeTask = [pscustomobject]@{{
    TaskName = $Spec.task_name
    TaskPath = $Spec.task_path
    Actions = @($FakeAction)
    Triggers = @($FakeTrigger)
    Principal = [pscustomobject]@{{
        UserId = $CurrentUser
        LogonType = 'Interactive'
        RunLevel = 'Limited'
    }}
    Settings = [pscustomobject]@{{
        Enabled = $true
        StartWhenAvailable = $false
        WakeToRun = $true
        DisallowStartIfOnBatteries = $false
        StopIfGoingOnBatteries = $false
        RunOnlyIfNetworkAvailable = $false
        MultipleInstances = 'IgnoreNew'
        ExecutionTimeLimit = 'PT0S'
    }}
}}
$ValidMismatches = @(Get-GrandeAlphaTaskContractMismatches $FakeTask)
if ($ValidMismatches.Count -ne 0) {{
    $ValidMismatches | ConvertTo-Json
    exit 10
}}
$FakeTask.Actions[0].Arguments = '-File tampered.ps1'
$FakeTask.Settings.StartWhenAvailable = $true
$TamperedMismatches = @(Get-GrandeAlphaTaskContractMismatches $FakeTask)
$TamperedMismatches | ConvertTo-Json
if ($TamperedMismatches.Count -ne 2) {{ exit 11 }}
"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    mismatches = json.loads(result.stdout)
    assert any("action arguments" in item for item in mismatches)
    assert any("late catch-up" in item for item in mismatches)
