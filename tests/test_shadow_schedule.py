from __future__ import annotations

import json
import os
import subprocess
import sys
from base64 import b64encode
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from grande_alpha.app import record_auto_shadow_heartbeat
from grande_alpha.controller import TradingSnapshot

ROOT = Path(__file__).resolve().parents[1]


def test_scheduled_launcher_is_auto_shadow_only_and_supervises_foreground_child() -> None:
    script = (ROOT / "scheduled-shadow.ps1").read_text(encoding="utf-8")
    helper = (ROOT / "shadow-lifecycle.ps1").read_text(encoding="utf-8")

    assert "Get-GrandeAlphaPython $ProjectRoot" in script
    assert "-m grande_alpha.app --auto-shadow" in script
    assert "Start-GrandeAlphaSuspendedJobProcess" in script
    assert "$Child.WaitForExit()" in script
    assert "Set-ScheduledShadowLifecycle -State 'running' -ChildProcess $Child" in script
    assert "$OwnedChild.Resume()" in script
    assert script.index("Start-GrandeAlphaSuspendedJobProcess") < script.index(
        "Set-ScheduledShadowLifecycle -State 'running' -ChildProcess $Child"
    ) < script.index("$OwnedChild.Resume()")
    assert "PROC_THREAD_ATTRIBUTE_JOB_LIST" in helper
    assert "CREATE_SUSPENDED" in helper
    assert "EXTENDED_STARTUPINFO_PRESENT" in helper
    assert "JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE" in helper
    assert "AssignProcessToJobObject" not in helper
    assert "scheduled-shadow-lifecycle.json" in script
    assert "instance_id = [guid]::NewGuid().ToString('D')" in script
    assert "wrapper_process_id = [int]$WrapperProcess.Id" in script
    assert "child_process_id" in script
    assert "Get-GrandeAlphaOwnedRuntime" in script
    assert "Local\\GRANDEAlpha.ScheduledShadow" in script
    assert "$InitialRestartDelaySeconds = 15" in script
    assert "$MaximumRestartDelaySeconds = 300" in script
    assert "$StableRuntimeSeconds = 300" in script
    assert "while ($true)" in script
    assert "if ($ExitCode -eq 0)" in script
    assert "supervisor will remain off" in script
    assert "Start-Sleep -Seconds $RestartDelaySeconds" in script
    assert "read-only supervisor restart" in script
    assert "Start-Process @" not in script
    assert "review_order" not in script
    assert "place_order" not in script
    assert "cancel_order" not in script


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Objects are Windows-only")
def test_wrapper_crash_before_manifest_resume_cannot_leave_unrecorded_child(tmp_path) -> None:
    helper = ROOT / "shadow-lifecycle.ps1"
    wrapper = tmp_path / "crash-wrapper.ps1"
    record_path = tmp_path / "owned-child.json"
    marker_path = tmp_path / "child-executed.txt"
    escaped_marker = str(marker_path).replace("'", "''")
    child_script = (
        f"Set-Content -LiteralPath '{escaped_marker}' -Value ran; "
        "Start-Sleep -Seconds 30"
    )
    encoded_child_script = b64encode(child_script.encode("utf-16-le")).decode("ascii")
    arguments = f"-NoProfile -NonInteractive -EncodedCommand {encoded_child_script}"
    escaped_helper = str(helper).replace("'", "''")
    escaped_working_directory = str(tmp_path).replace("'", "''")
    escaped_record = str(record_path).replace("'", "''")
    escaped_arguments = arguments.replace("'", "''")
    wrapper.write_text(
        rf"""
. '{escaped_helper}'
$Executable = (Get-Command powershell.exe -ErrorAction Stop).Source
$Owned = Start-GrandeAlphaSuspendedJobProcess `
    -Executable $Executable `
    -Arguments '{escaped_arguments}' `
    -WorkingDirectory '{escaped_working_directory}'
$Snapshot = Get-GrandeAlphaProcessSnapshot ([int]$Owned.Process.Id)
$StartedAt = $Owned.Process.StartTime.ToUniversalTime().ToString('o')
$ExpectedCommandLine = '"' + $Executable + '" {escaped_arguments}'
[ordered]@{{
    process_id = [int]$Owned.Process.Id
    started_at_utc = $StartedAt
    executable = $Executable
    arguments = '{escaped_arguments}'
    pid_and_start_recorded = (
        [int]$Snapshot.process_id -eq [int]$Owned.Process.Id -and
        [datetimeoffset]::Parse([string]$Snapshot.started_at_utc).UtcDateTime.Ticks -eq
            [datetimeoffset]::Parse($StartedAt).UtcDateTime.Ticks
    )
    command_line_recorded = [string]::Equals(
        [string]$Snapshot.command_line,
        $ExpectedCommandLine,
        [StringComparison]::Ordinal
    )
    owner_recorded = [string]::Equals(
        [string]$Snapshot.owner_sid,
        [Security.Principal.WindowsIdentity]::GetCurrent().User.Value,
        [StringComparison]::OrdinalIgnoreCase
    )
    direct_wrapper_child = [int]$Snapshot.parent_process_id -eq $PID
}} | ConvertTo-Json | Set-Content -LiteralPath '{escaped_record}' -Encoding utf8
[Diagnostics.Process]::GetCurrentProcess().Kill()
Start-Sleep -Seconds 30
""",
        encoding="utf-8",
    )

    crashed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(wrapper),
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert crashed.returncode != 0
    assert record_path.is_file(), crashed.stderr
    child = json.loads(record_path.read_text(encoding="utf-8-sig"))
    assert child["pid_and_start_recorded"] is True
    assert child["command_line_recorded"] is True
    assert child["owner_recorded"] is True
    assert child["direct_wrapper_child"] is True

    escaped_started = child["started_at_utc"].replace("'", "''")
    escaped_executable = child["executable"].replace("'", "''")
    command = rf"""
. '{escaped_helper}'
$Deadline = [datetimeoffset]::UtcNow.AddSeconds(10)
do {{
    $Snapshot = Get-GrandeAlphaProcessSnapshot {int(child['process_id'])}
    if ($null -eq $Snapshot) {{ break }}
    Start-Sleep -Milliseconds 50
}} while ([datetimeoffset]::UtcNow -lt $Deadline)
$WasStillAlive = $null -ne $Snapshot
$Exact = $false
if ($WasStillAlive) {{
    $Exact = Test-GrandeAlphaProcessIdentity `
        -Snapshot $Snapshot `
        -ExpectedProcessId {int(child['process_id'])} `
        -ExpectedStartedAtUtc '{escaped_started}' `
        -ExpectedExecutable '{escaped_executable}' `
        -ExpectedArguments '{escaped_arguments}' `
        -ExpectedOwnerSid ([Security.Principal.WindowsIdentity]::GetCurrent().User.Value)
    if ($Exact) {{
        $Snapshot.native_process.Kill()
        $Snapshot.native_process.WaitForExit(5000)
    }}
}}
[pscustomobject]@{{was_still_alive=$WasStillAlive; exact_if_alive=$Exact}} | ConvertTo-Json -Compress
"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    containment = json.loads(result.stdout)

    assert containment["was_still_alive"] is False
    assert marker_path.exists() is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Objects are Windows-only")
def test_suspended_job_child_is_exact_after_manifest_barrier_and_resume(tmp_path) -> None:
    helper = ROOT / "shadow-lifecycle.ps1"
    marker_path = tmp_path / "resumed.txt"
    escaped_marker = str(marker_path).replace("'", "''")
    child_script = (
        f"Set-Content -LiteralPath '{escaped_marker}' -Value ran; "
        "Start-Sleep -Seconds 30"
    )
    encoded_child_script = b64encode(child_script.encode("utf-16-le")).decode("ascii")
    arguments = f"-NoProfile -NonInteractive -EncodedCommand {encoded_child_script}"
    escaped_helper = str(helper).replace("'", "''")
    escaped_arguments = arguments.replace("'", "''")
    escaped_working_directory = str(tmp_path).replace("'", "''")
    command = rf"""
. '{escaped_helper}'
$Executable = (Get-Command powershell.exe -ErrorAction Stop).Source
$Owned = Start-GrandeAlphaSuspendedJobProcess `
    -Executable $Executable `
    -Arguments '{escaped_arguments}' `
    -WorkingDirectory '{escaped_working_directory}'
$ProcessId = [int]$Owned.Process.Id
$StartedAt = $Owned.Process.StartTime.ToUniversalTime().ToString('o')
$RanBeforeResume = Test-Path -LiteralPath '{escaped_marker}' -PathType Leaf
$ExactAfterResume = $false
$DirectChild = $false
try {{
    $Owned.Resume()
    $Deadline = [datetimeoffset]::UtcNow.AddSeconds(10)
    do {{
        $Snapshot = Get-GrandeAlphaProcessSnapshot $ProcessId
        if ($null -ne $Snapshot) {{
            $ExactAfterResume = Test-GrandeAlphaProcessIdentity `
                -Snapshot $Snapshot `
                -ExpectedProcessId $ProcessId `
                -ExpectedStartedAtUtc $StartedAt `
                -ExpectedExecutable $Executable `
                -ExpectedArguments '{escaped_arguments}' `
                -ExpectedOwnerSid ([Security.Principal.WindowsIdentity]::GetCurrent().User.Value)
            $DirectChild = [int]$Snapshot.parent_process_id -eq $PID
        }}
        if ($ExactAfterResume -and (Test-Path -LiteralPath '{escaped_marker}' -PathType Leaf)) {{ break }}
        Start-Sleep -Milliseconds 50
    }} while ([datetimeoffset]::UtcNow -lt $Deadline)
}} finally {{
    $Owned.Dispose()
}}
$ExitDeadline = [datetimeoffset]::UtcNow.AddSeconds(10)
do {{
    $StillAlive = $null -ne (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
    if (-not $StillAlive) {{ break }}
    Start-Sleep -Milliseconds 50
}} while ([datetimeoffset]::UtcNow -lt $ExitDeadline)
[pscustomobject]@{{
    ran_before_resume=$RanBeforeResume
    ran_after_resume=(Test-Path -LiteralPath '{escaped_marker}' -PathType Leaf)
    exact_after_resume=$ExactAfterResume
    direct_wrapper_child=$DirectChild
    alive_after_dispose=$StillAlive
}} | ConvertTo-Json -Compress
"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=40,
    )
    lifecycle = json.loads(result.stdout)

    assert lifecycle == {
        "ran_before_resume": False,
        "ran_after_resume": True,
        "exact_after_resume": True,
        "direct_wrapper_child": True,
        "alive_after_dispose": False,
    }


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
        "RestartCount = $Spec.task_restart_count",
        "RestartInterval = [TimeSpan]::FromMinutes($Spec.task_restart_interval_minutes)",
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
        "Get-GrandeAlphaHeartbeatStatus",
        "Get-GrandeAlphaInstalledRuntime",
        "Resolve-GrandeAlphaRuntime",
        "Test-GrandeAlphaSnapshotStillExact",
        "CloseMainWindow()",
        ".native_process.Kill()",
        "Start-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath",
        "Stop-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath",
        "[switch]$Start",
        "[switch]$Stop",
        "[switch]$Restart",
        "scheduled-shadow-heartbeat.json",
        "scheduled-shadow-lifecycle.json",
        "broker_writes -eq $false",
        "live_authority -eq $false",
    }
    for item in required:
        assert item in script
    assert "Password" not in script
    assert "--auto-shadow" in script
    assert "Get-Process python" not in script
    assert "taskkill" not in script.lower()
    assert "Win32_Process" not in script


def test_local_installer_adds_setup_shortcut_without_registering_task() -> None:
    script = (ROOT / "install-local.ps1").read_text(encoding="utf-8")
    setup = (ROOT / "Scheduled Shadow Setup.cmd").read_text(encoding="utf-8")

    assert "Scheduled Shadow Setup.cmd" in script
    assert "GRANDE Alpha Shadow Schedule.lnk" in script
    assert "No task was enabled by this installer" in script
    assert "Register-ScheduledTask" not in script
    for mode in ("-Start", "-Stop", "-Restart", "-Status", "-Install", "-Remove"):
        assert mode in setup


def test_source_release_contains_schedule_management_and_launcher() -> None:
    script = (ROOT / "release.ps1").read_text(encoding="utf-8")

    assert "archive --format=zip" in script
    assert (ROOT / "manage-shadow-schedule.ps1").is_file()
    assert (ROOT / "scheduled-shadow.ps1").is_file()
    assert (ROOT / "shadow-lifecycle.ps1").is_file()
    assert (ROOT / "Scheduled Shadow Setup.cmd").is_file()


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
    assert definition["task_restart_count"] == 3
    assert definition["task_restart_interval_minutes"] == 1
    assert definition["application_mode"] == "--auto-shadow"
    assert definition["supervisor_restart_initial_seconds"] == 15
    assert definition["supervisor_restart_maximum_seconds"] == 300
    assert definition["heartbeat_interval_seconds"] == 60
    assert definition["heartbeat_stale_after_seconds"] == 180
    assert definition["lifecycle_schema_version"] == 1
    assert definition["start_timeout_seconds"] == 45
    assert definition["normal_close_timeout_seconds"] == 10
    assert definition["stop_timeout_seconds"] == 30
    assert definition["post_stop_reconcile_seconds"] == 15
    assert Path(definition["heartbeat_path"]).is_absolute()
    assert Path(definition["lifecycle_path"]).is_absolute()
    assert Path(definition["python_launcher"]).is_absolute()
    assert Path(definition["python_process_executable"]).is_absolute()
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
        RestartCount = 3
        RestartInterval = 'PT1M'
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


@pytest.mark.skipif(sys.platform != "win32", reason="Windows task identity is Windows-only")
def test_install_remove_identity_allows_setting_repair_but_rejects_action_drift() -> None:
    script = ROOT / "manage-shadow-schedule.ps1"
    command = rf"""
. '{script}'
$FakeTask = [pscustomobject]@{{
    TaskName = $Spec.task_name
    TaskPath = $Spec.task_path
    Actions = @([pscustomobject]@{{
        Execute = $Spec.execute
        Arguments = $Spec.arguments
        WorkingDirectory = $Spec.working_directory
    }})
    Principal = [pscustomobject]@{{
        UserId = $CurrentUser
        LogonType = 'Interactive'
        RunLevel = 'Limited'
    }}
}}
$Results = [ordered]@{{}}
$Results.setting_drift = @(Get-GrandeAlphaTaskIdentityMismatches $FakeTask).Count
$FakeTask.Actions[0].Arguments = '-File unrelated.ps1'
$Results.action_drift = @(Get-GrandeAlphaTaskIdentityMismatches $FakeTask).Count
$Results | ConvertTo-Json -Compress
"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    identity = json.loads(result.stdout)

    assert identity["setting_drift"] == 0
    assert identity["action_drift"] == 1


@pytest.mark.skipif(sys.platform != "win32", reason="Windows disabled task state is Windows-only")
def test_install_remove_disabled_task_path_requires_proven_runtime_off() -> None:
    manager = ROOT / "manage-shadow-schedule.ps1"
    command = rf"""
. '{manager}'
$Task = [pscustomobject]@{{State='Disabled'}}
$script:RuntimeState = 'STOPPED'
$script:HeartbeatAlive = $false
$script:Cleaned = $false
function Get-GrandeAlphaHeartbeatStatus {{
    [pscustomobject]@{{
        heartbeat_present=$false; process_id_valid=$false
        process_alive=$script:HeartbeatAlive; liveness='MISSING'; process_id=$null
    }}
}}
function Resolve-GrandeAlphaRuntime {{
    [pscustomobject]@{{state=$script:RuntimeState; detail='test'; child_identity_valid=$false}}
}}
function Stop-GrandeAlphaAbsentTaskRuntime {{
    $script:Cleaned = $true
    $script:RuntimeState = 'STOPPED'
    $script:HeartbeatAlive = $false
}}
$Heartbeat = Get-GrandeAlphaHeartbeatStatus
$Runtime = Resolve-GrandeAlphaRuntime $Heartbeat
Stop-GrandeAlphaLifecycleForTaskMutation `
    -Task $Task -Runtime $Runtime -HeartbeatStatus $Heartbeat -Operation '-Remove'
if ($script:Cleaned) {{ exit 20 }}
$script:RuntimeState = 'ORPHANED'
$Runtime = Resolve-GrandeAlphaRuntime (Get-GrandeAlphaHeartbeatStatus)
Stop-GrandeAlphaLifecycleForTaskMutation `
    -Task $Task -Runtime $Runtime -HeartbeatStatus (Get-GrandeAlphaHeartbeatStatus) -Operation '-Install'
if (-not $script:Cleaned) {{ exit 21 }}
$script:RuntimeState = 'STOPPED'
$script:HeartbeatAlive = $true
$Blocked = $false
try {{
    Stop-GrandeAlphaLifecycleForTaskMutation `
        -Task $Task `
        -Runtime (Resolve-GrandeAlphaRuntime (Get-GrandeAlphaHeartbeatStatus)) `
        -HeartbeatStatus (Get-GrandeAlphaHeartbeatStatus) `
        -Operation '-Remove'
}} catch {{ $Blocked = $true }}
if (-not $Blocked) {{ exit 22 }}
exit 0
"""
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    source = manager.read_text(encoding="utf-8")
    assert source.count("Stop-GrandeAlphaLifecycleForTaskMutation") >= 3


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Scheduled Tasks are Windows-only")
def test_heartbeat_status_separates_liveness_from_operational_state(tmp_path) -> None:
    script = ROOT / "manage-shadow-schedule.ps1"
    heartbeat_path = tmp_path / "scheduled-shadow-heartbeat.json"
    now = datetime.now(UTC)

    def status() -> dict[str, object]:
        escaped_script = str(script).replace("'", "''")
        escaped_heartbeat = str(heartbeat_path).replace("'", "''")
        command = rf"""
. '{escaped_script}'
$HeartbeatPath = '{escaped_heartbeat}'
Get-GrandeAlphaHeartbeatStatus | ConvertTo-Json -Compress
"""
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout)

    active = TradingSnapshot(
        connected=True,
        shadow_running=True,
        last_refresh=now - timedelta(seconds=2),
        last_reconcile_at=now - timedelta(seconds=3),
        shadow_equity=50.5,
        shadow_pnl=0.5,
        shadow_fills=2,
    )
    record_auto_shadow_heartbeat(
        heartbeat_path,
        state="running",
        observed_at=now,
        process_id=os.getpid(),
        session_open=True,
        snapshot=active,
    )
    active_status = status()
    assert active_status["liveness"] == "EVENT_LOOP_FRESH"
    assert active_status["operational_state"] == "ACTIVE"
    assert active_status["connected"] is True
    assert active_status["shadow_running"] is True
    assert active_status["shadow_fills"] == 2

    record_auto_shadow_heartbeat(
        heartbeat_path,
        state="running",
        observed_at=now,
        process_id=os.getpid(),
        session_open=True,
        snapshot=TradingSnapshot(),
    )
    degraded_status = status()
    assert degraded_status["liveness"] == "EVENT_LOOP_FRESH"
    assert degraded_status["operational_state"] == "DEGRADED"
    assert degraded_status["connected"] is False
    assert degraded_status["shadow_running"] is False

    record_auto_shadow_heartbeat(
        heartbeat_path,
        state="running",
        observed_at=now,
        process_id=os.getpid(),
        session_open=False,
        snapshot=TradingSnapshot(),
    )
    waiting_status = status()
    assert waiting_status["liveness"] == "EVENT_LOOP_FRESH"
    assert waiting_status["operational_state"] == "WAITING"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows heartbeat PID checks are Windows-only")
def test_invalid_heartbeat_preserves_live_pid_and_malformed_pid_blocks_mutation(tmp_path) -> None:
    manager = ROOT / "manage-shadow-schedule.ps1"
    heartbeat_path = tmp_path / "scheduled-shadow-heartbeat.json"

    def status(payload: dict[str, object]) -> dict[str, object]:
        heartbeat_path.write_text(json.dumps(payload), encoding="utf-8")
        escaped_manager = str(manager).replace("'", "''")
        escaped_heartbeat = str(heartbeat_path).replace("'", "''")
        command = rf"""
. '{escaped_manager}'
$HeartbeatPath = '{escaped_heartbeat}'
Get-GrandeAlphaHeartbeatStatus | ConvertTo-Json -Compress
"""
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout)

    base = {
        "schema_version": 1,
        "mode": "--auto-shadow",
        "liveness_source": "qt_event_loop_timer",
        "read_only": True,
        "broker_writes": False,
        "live_authority": False,
        "state": "running",
        "process_id": os.getpid(),
        "observed_at_utc": "not-a-timestamp",
        "runtime": {},
    }
    malformed_timestamp = status(base)
    assert malformed_timestamp["liveness"] == "INVALID"
    assert malformed_timestamp["heartbeat_present"] is True
    assert malformed_timestamp["process_id_valid"] is True
    assert malformed_timestamp["process_id"] == os.getpid()
    assert malformed_timestamp["process_alive"] is True

    runtime_failure = dict(base)
    runtime_failure["observed_at_utc"] = datetime.now(UTC).isoformat()
    runtime_failure["runtime"] = {
        "connected": False,
        "shadow_running": False,
        "session_open": False,
        "last_refresh_utc": None,
        "last_reconcile_at_utc": None,
        "shadow_equity": "not-a-number",
        "shadow_pnl": 0,
        "shadow_fills": 0,
    }
    malformed_runtime = status(runtime_failure)
    assert malformed_runtime["liveness"] == "INVALID"
    assert malformed_runtime["process_id"] == os.getpid()
    assert malformed_runtime["process_alive"] is True

    malformed_pid = dict(base)
    malformed_pid["process_id"] = str(os.getpid())
    malformed_pid_status = status(malformed_pid)
    assert malformed_pid_status["liveness"] == "INVALID"
    assert malformed_pid_status["heartbeat_present"] is True
    assert malformed_pid_status["process_id_valid"] is False
    assert malformed_pid_status["process_id"] is None
    assert malformed_pid_status["process_alive"] is False

    escaped_manager = str(manager).replace("'", "''")
    escaped_heartbeat = str(heartbeat_path).replace("'", "''")
    command = rf"""
. '{escaped_manager}'
$HeartbeatPath = '{escaped_heartbeat}'
$script:Resolved = $false
function Resolve-GrandeAlphaRuntime {{ $script:Resolved = $true }}
try {{
    [void](Invoke-GrandeAlphaAbsentTaskPreflight '-Install')
    exit 20
}} catch {{
    if ($script:Resolved) {{ exit 21 }}
}}
exit 0
"""
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell lifecycle identity is Windows-only")
def test_lifecycle_identity_requires_pid_start_executable_argv_and_owner() -> None:
    helper = ROOT / "shadow-lifecycle.ps1"
    command = rf"""
. '{helper}'
$Started = [datetimeoffset]::UtcNow.ToString('o')
$Snapshot = [pscustomobject]@{{
    process_id = 4321
    parent_process_id = 1234
    executable_path = 'C:\Runtime\python.exe'
    command_line = '"C:\Runtime\python.exe" -m grande_alpha.app --auto-shadow'
    owner_sid = 'S-1-5-21-1000'
    started_at_utc = $Started
}}
function Test-One($Value) {{
    Test-GrandeAlphaProcessIdentity `
        -Snapshot $Value `
        -ExpectedProcessId 4321 `
        -ExpectedStartedAtUtc $Started `
        -ExpectedExecutable 'C:\Runtime\python.exe' `
        -ExpectedArguments '-m grande_alpha.app --auto-shadow' `
        -ExpectedOwnerSid 'S-1-5-21-1000'
}}
$Results = [ordered]@{{}}
$Results.valid = Test-One $Snapshot
$Changed = $Snapshot.PSObject.Copy(); $Changed.process_id = 9999
$Results.pid = Test-One $Changed
$Changed = $Snapshot.PSObject.Copy(); $Changed.started_at_utc = ([datetimeoffset]::UtcNow.AddSeconds(-5)).ToString('o')
$Results.start = Test-One $Changed
$Changed = $Snapshot.PSObject.Copy(); $Changed.started_at_utc = ([datetimeoffset]::Parse($Started).AddMilliseconds(900)).ToString('o')
$Results.subsecond_start = Test-One $Changed
$Changed = $Snapshot.PSObject.Copy(); $Changed.executable_path = 'C:\Other\python.exe'
$Results.executable = Test-One $Changed
$Changed = $Snapshot.PSObject.Copy(); $Changed.command_line = '"C:\Runtime\python.exe" -m other.app --auto-shadow'
$Results.argv = Test-One $Changed
$Changed = $Snapshot.PSObject.Copy(); $Changed.command_line = '"C:\RUNTIME\PYTHON.EXE" -m grande_alpha.app --AUTO-SHADOW'
$Results.argv_case = Test-One $Changed
$Changed = $Snapshot.PSObject.Copy(); $Changed.owner_sid = 'S-1-5-21-2000'
$Results.owner = Test-One $Changed
$Results | ConvertTo-Json -Compress
"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    identities = json.loads(result.stdout)

    assert identities == {
        "valid": True,
        "pid": False,
        "start": False,
        "subsecond_start": False,
        "executable": False,
        "argv": False,
        "argv_case": False,
        "owner": False,
    }


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell lifecycle contract is Windows-only")
def test_lifecycle_manifest_contract_is_exact_and_resolves_venv_process_image() -> None:
    helper = ROOT / "shadow-lifecycle.ps1"
    python_launcher = ROOT / ".venv" / "Scripts" / "python.exe"
    command = rf"""
. '{helper}'
$Root = '{ROOT}'
$Launcher = Join-Path $Root 'scheduled-shadow.ps1'
$PythonLauncher = '{python_launcher}'
$PythonProcess = Get-GrandeAlphaPythonProcessExecutable $PythonLauncher
$Now = [datetimeoffset]::UtcNow.ToString('o')
function New-Record {{
    [pscustomobject]@{{
        schema_version = 1
        instance_id = [guid]::NewGuid().ToString('D')
        task_name = 'GRANDE Alpha Live Shadow'
        mode = '--auto-shadow'
        read_only = $true
        broker_writes = $false
        live_authority = $false
        owner_sid = 'S-1-5-21-1000'
        project_root = $Root
        launcher_path = $Launcher
        python_executable = $PythonLauncher
        python_process_executable = $PythonProcess
        wrapper_process_id = 100
        wrapper_started_at_utc = $Now
        child_process_id = 200
        child_started_at_utc = $Now
        state = 'running'
        observed_at_utc = $Now
    }}
}}
function Test-Record($Record) {{
    Test-GrandeAlphaLifecycleRecord `
        -Record $Record `
        -ExpectedProjectRoot $Root `
        -ExpectedLauncher $Launcher `
        -ExpectedPythonLauncher $PythonLauncher `
        -ExpectedPythonProcessExecutable $PythonProcess `
        -ExpectedOwnerSid 'S-1-5-21-1000' `
        -ExpectedTaskName 'GRANDE Alpha Live Shadow'
}}
$Results = [ordered]@{{}}
$Record = New-Record; $Results.valid = Test-Record $Record
$Record = New-Record; $Record.state = 'mystery'; $Results.state = Test-Record $Record
$Record = New-Record; $Record.project_root = '.'; $Results.relative_path = Test-Record $Record
$Record = New-Record; $Record.broker_writes = $true; $Results.write_flag = Test-Record $Record
$Record = New-Record; $Record.child_started_at_utc = $null; $Results.child_pair = Test-Record $Record
$Record = New-Record; $Record.child_started_at_utc = ([datetimeoffset]::Parse($Now).AddTicks(-1)).ToString('o'); $Results.start_order = Test-Record $Record
$Results.python_process_executable = $PythonProcess
$Results | ConvertTo-Json -Compress
"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    contract = json.loads(result.stdout)

    assert contract["valid"] is True
    assert contract["state"] is False
    assert contract["relative_path"] is False
    assert contract["write_flag"] is False
    assert contract["child_pair"] is False
    assert contract["start_order"] is False
    assert Path(contract["python_process_executable"]).is_absolute()
    assert Path(contract["python_process_executable"]).is_file()


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell process trees are Windows-only")
def test_owned_launcher_must_be_direct_time_ordered_wrapper_child() -> None:
    helper = ROOT / "shadow-lifecycle.ps1"
    command = rf"""
. '{helper}'
$WrapperStarted = [datetimeoffset]::UtcNow.AddSeconds(-2).ToString('o')
$ChildStarted = [datetimeoffset]::UtcNow.AddSeconds(-1).ToString('o')
$script:ChildParent = 10
$Record = [pscustomobject]@{{
    schema_version=1
    instance_id=[guid]::NewGuid().ToString('D')
    task_name='GRANDE Alpha Live Shadow'
    mode='--auto-shadow'
    read_only=$true
    broker_writes=$false
    live_authority=$false
    owner_sid='S-1-5-21-1000'
    project_root='C:\Product'
    launcher_path='C:\Product\scheduled-shadow.ps1'
    python_executable='C:\Product\.venv\Scripts\python.exe'
    python_process_executable='C:\Python\python.exe'
    wrapper_process_id=10
    wrapper_started_at_utc=$WrapperStarted
    child_process_id=20
    child_started_at_utc=$ChildStarted
    state='running'
    observed_at_utc=[datetimeoffset]::UtcNow.ToString('o')
}}
function Read-GrandeAlphaJsonFile {{ $Record }}
function Get-GrandeAlphaProcessSnapshot([int]$ProcessId) {{
    if ($ProcessId -eq 10) {{
        [pscustomobject]@{{
            process_id=10; parent_process_id=1
            executable_path='C:\Windows\powershell.exe'
            command_line='"C:\Windows\powershell.exe" -File "C:\Product\scheduled-shadow.ps1"'
            owner_sid='S-1-5-21-1000'; started_at_utc=$WrapperStarted
        }}
    }} elseif ($ProcessId -eq 20) {{
        [pscustomobject]@{{
            process_id=20; parent_process_id=$script:ChildParent
            executable_path='C:\Product\.venv\Scripts\python.exe'
            command_line='"C:\Product\.venv\Scripts\python.exe" -m grande_alpha.app --auto-shadow'
            owner_sid='S-1-5-21-1000'; started_at_utc=$ChildStarted
        }}
    }}
}}
function Runtime-State {{
    (Get-GrandeAlphaOwnedRuntime `
        -LifecyclePath 'C:\Lifecycle.json' `
        -ProjectRoot 'C:\Product' `
        -Launcher 'C:\Product\scheduled-shadow.ps1' `
        -PythonLauncher 'C:\Product\.venv\Scripts\python.exe' `
        -PythonProcessExecutable 'C:\Python\python.exe' `
        -PowerShellExe 'C:\Windows\powershell.exe' `
        -ActionArguments '-File "C:\Product\scheduled-shadow.ps1"' `
        -CurrentUserSid 'S-1-5-21-1000' `
        -TaskName 'GRANDE Alpha Live Shadow').state
}}
$Results = [ordered]@{{}}
$Results.direct = Runtime-State
$script:ChildParent = 999
$Results.wrong_parent = Runtime-State
$Results | ConvertTo-Json -Compress
"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    tree = json.loads(result.stdout)

    assert tree == {"direct": "OWNED", "wrong_parent": "UNVERIFIED"}


def test_schedule_lifecycle_controls_fail_closed_and_report_ready_orphans() -> None:
    manager = (ROOT / "manage-shadow-schedule.ps1").read_text(encoding="utf-8")
    helper = (ROOT / "shadow-lifecycle.ps1").read_text(encoding="utf-8")

    assert "Runtime ownership:" in manager
    assert "The task is Ready, but runtime ownership is" in manager
    assert "A live heartbeat is not safely attributable" in manager
    assert "The recorded child identity changed during stop" in manager
    assert manager.count("Test-GrandeAlphaSnapshotStillExact $ChildSnapshot") >= 2
    assert "Get-CimInstance -ClassName Win32_Process" in helper
    assert '-Filter "ParentProcessId = $ParentProcessId"' in helper
    assert "Get-GrandeAlphaDirectChildProcessSnapshots" in helper
    assert "Invoke-CimMethod -InputObject $CimProcess -MethodName GetOwnerSid" in helper
    assert "ExpectedStartedAtUtc" in helper
    assert "ExpectedExecutable" in helper
    assert "ExpectedArguments" in helper
    assert "ExpectedOwnerSid" in helper
    assert "Get-CimInstance -ClassName Win32_Process" not in manager
    assert "Where-Object" not in helper


@pytest.mark.skipif(sys.platform != "win32", reason="Windows status rendering is Windows-only")
def test_ready_status_rejects_owned_or_fresh_unattributed_process() -> None:
    manager = ROOT / "manage-shadow-schedule.ps1"
    command = rf"""
. '{manager}'
$FakeAction = [pscustomobject]@{{
    Execute = $Spec.execute
    Arguments = $Spec.arguments
    WorkingDirectory = $Spec.working_directory
}}
$FakeTrigger = [pscustomobject]@{{
    StartBoundary = "2026-08-19T$($Spec.local_time):00"
    Enabled = $true
}}
$FakeTask = [pscustomobject]@{{
    State = 'Ready'
    Principal = [pscustomobject]@{{UserId='current'; LogonType='Interactive'; RunLevel='Limited'}}
    Actions = @($FakeAction)
    Triggers = @($FakeTrigger)
    Settings = [pscustomobject]@{{
        StartWhenAvailable = $false
        WakeToRun = $true
        DisallowStartIfOnBatteries = $false
        StopIfGoingOnBatteries = $false
        RunOnlyIfNetworkAvailable = $false
        MultipleInstances = 'IgnoreNew'
        RestartCount = 3
        RestartInterval = 'PT1M'
    }}
}}
function Get-GrandeAlphaScheduledTask {{ $FakeTask }}
function Get-GrandeAlphaTaskContractMismatches($Task) {{ @() }}
function Get-ScheduledTaskInfo {{ [pscustomobject]@{{NextRunTime='tomorrow'}} }}
function Get-GrandeAlphaApplicationRuntime {{
    [pscustomobject]@{{state='NONE'; detail='none'; process=$null; identity_valid=$false}}
}}
$script:RuntimeState = 'ORPHANED'
$script:HeartbeatAlive = $false
$script:HeartbeatLiveness = 'EVENT_LOOP_FRESH'
function Resolve-GrandeAlphaRuntime {{
    [pscustomobject]@{{
        state=$script:RuntimeState
        detail='test runtime'
        child=$null
        child_identity_valid=$false
    }}
}}
function Get-GrandeAlphaHeartbeatStatus {{
    [pscustomobject]@{{
        liveness=if($script:HeartbeatAlive){{$script:HeartbeatLiveness}}else{{'MISSING'}}
        operational_state='DEGRADED'
        state=if($script:HeartbeatAlive){{'running'}}else{{$null}}
        age_seconds=0
        process_id=999
        process_alive=$script:HeartbeatAlive
        connected=$false
        shadow_running=$false
        session_open=$false
        shadow_equity=$null
        shadow_pnl=$null
        shadow_fills=$null
        last_refresh_age_seconds=$null
        last_reconcile_age_seconds=$null
        detail='test heartbeat'
    }}
}}
if (Write-ScheduleStatus) {{ exit 10 }}
$script:RuntimeState = 'STOPPED'
$script:HeartbeatAlive = $true
if (Write-ScheduleStatus) {{ exit 11 }}
$script:HeartbeatLiveness = 'STALE'
if (Write-ScheduleStatus) {{ exit 12 }}
$script:HeartbeatLiveness = 'INVALID'
if (Write-ScheduleStatus) {{ exit 13 }}
$script:HeartbeatAlive = $false
if (-not (Write-ScheduleStatus)) {{ exit 14 }}
"""
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows queued task state is Windows-only")
def test_stop_cancels_queued_task_and_status_never_treats_queued_as_off() -> None:
    manager = ROOT / "manage-shadow-schedule.ps1"
    command = rf"""
. '{manager}'
$FakeTask = [pscustomobject]@{{
    State = 'Queued'
    Principal = [pscustomobject]@{{UserId='current'; LogonType='Interactive'; RunLevel='Limited'}}
    Actions = @([pscustomobject]@{{
        Execute=$Spec.execute; Arguments=$Spec.arguments; WorkingDirectory=$Spec.working_directory
    }})
    Triggers = @([pscustomobject]@{{StartBoundary='2026-08-19T06:20:00'; Enabled=$true}})
    Settings = [pscustomobject]@{{
        StartWhenAvailable=$false; WakeToRun=$true; DisallowStartIfOnBatteries=$false
        StopIfGoingOnBatteries=$false; RunOnlyIfNetworkAvailable=$false
        MultipleInstances='IgnoreNew'; RestartCount=3; RestartInterval='PT1M'
    }}
}}
$script:StopCalled = $false
function Get-GrandeAlphaScheduledTask {{ $FakeTask }}
function Stop-ScheduledTask {{ $script:StopCalled = $true; $FakeTask.State = 'Ready' }}
function Get-GrandeAlphaHeartbeatStatus {{
    [pscustomobject]@{{
        heartbeat_present=$false; process_id_valid=$false; process_alive=$false
        liveness='MISSING'; operational_state='DEGRADED'; state=$null; age_seconds=$null
        process_id=$null; connected=$false; shadow_running=$false; session_open=$false
        shadow_equity=$null; shadow_pnl=$null; shadow_fills=$null
        last_refresh_age_seconds=$null; last_reconcile_age_seconds=$null; detail='none'
    }}
}}
function Get-GrandeAlphaInstalledRuntime {{
    [pscustomobject]@{{state='STOPPED'; child_identity_valid=$false; child=$null}}
}}
function Resolve-GrandeAlphaRuntime {{
    [pscustomobject]@{{state='STOPPED'; detail='stopped'; child_identity_valid=$false; child=$null}}
}}
function Get-GrandeAlphaApplicationRuntime {{
    [pscustomobject]@{{state='NONE'; detail='none'; process=$null; identity_valid=$false}}
}}
function Get-GrandeAlphaTaskContractMismatches {{ @() }}
function Get-ScheduledTaskInfo {{ [pscustomobject]@{{NextRunTime='tomorrow'}} }}
$Runtime = [pscustomobject]@{{
    state='STOPPED'; detail='stopped'; child_identity_valid=$false; child=$null
}}
Stop-GrandeAlphaLifecycle -Task $FakeTask -Runtime $Runtime
if (-not $script:StopCalled -or $FakeTask.State -ne 'Ready') {{ exit 30 }}
$FakeTask.State = 'Queued'
if (Write-ScheduleStatus) {{ exit 31 }}
"""
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows lifecycle races are Windows-only")
def test_stop_reconciles_child_spawned_after_initial_runtime_snapshot() -> None:
    manager = ROOT / "manage-shadow-schedule.ps1"
    command = rf"""
. '{manager}'
$script:Task = [pscustomobject]@{{State='Running'}}
$script:TaskStopped = $false
$script:LateStopped = $false
function Stop-ScheduledTask {{
    $script:TaskStopped = $true
    $script:Task.State = 'Ready'
}}
function Get-GrandeAlphaScheduledTask {{ $script:Task }}
function Get-GrandeAlphaHeartbeatStatus {{
    [pscustomobject]@{{
        liveness=if(-not $script:TaskStopped){{'MISSING'}}elseif($script:LateStopped){{'STOPPED'}}else{{'EVENT_LOOP_FRESH'}}
        state=if(-not $script:TaskStopped){{$null}}elseif($script:LateStopped){{'stopped'}}else{{'running'}}
        process_alive=$script:TaskStopped -and -not $script:LateStopped
        process_id=333
    }}
}}
function Get-GrandeAlphaInstalledRuntime {{
    if ($script:LateStopped) {{
        [pscustomobject]@{{state='STOPPED'; child_identity_valid=$false; child=$null}}
    }} else {{
        [pscustomobject]@{{state='ORPHANED'; child_identity_valid=$true; child=[pscustomobject]@{{process_id=222}}}}
    }}
}}
function Get-GrandeAlphaApplicationRuntime {{
    [pscustomobject]@{{state='VERIFIED'; process=[pscustomobject]@{{process_id=333}}; identity_valid=$true}}
}}
function Stop-GrandeAlphaLateOwnedRuntime {{
    $script:LateStopped = $true
}}
$InitialRuntime = [pscustomobject]@{{
    state='STARTING'
    child_identity_valid=$false
    child=$null
    detail='wrapper had not recorded a child yet'
}}
Stop-GrandeAlphaLifecycle -Task $script:Task -Runtime $InitialRuntime
if (-not $script:TaskStopped) {{ exit 20 }}
if (-not $script:LateStopped) {{ exit 21 }}
"""
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows suspended launch state is Windows-only")
def test_stop_accepts_exact_recorded_launcher_before_application_child_exists() -> None:
    manager = ROOT / "manage-shadow-schedule.ps1"
    command = rf"""
. '{manager}'
$script:Task = [pscustomobject]@{{State='Running'}}
$script:Stopped = $false
$script:LateStopped = $false
function Stop-ScheduledTask {{ $script:Stopped = $true; $script:Task.State = 'Ready' }}
function Get-GrandeAlphaScheduledTask {{ $script:Task }}
function Get-GrandeAlphaHeartbeatStatus {{
    [pscustomobject]@{{
        heartbeat_present=$false; process_id_valid=$false; process_alive=$false
        liveness='MISSING'; state=$null; process_id=$null
    }}
}}
function Get-GrandeAlphaApplicationRuntime {{
    [pscustomobject]@{{state='NONE'; detail='base Python has not spawned'; process=$null}}
}}
function Wait-GrandeAlphaLaunchProcessExit {{ $true }}
function Get-GrandeAlphaInstalledRuntime {{
    if ($script:LateStopped) {{
        [pscustomobject]@{{state='STOPPED'; child_identity_valid=$false; child=$null}}
    }} else {{
        [pscustomobject]@{{state='ORPHANED'; child_identity_valid=$true; child=$Launch}}
    }}
}}
function Stop-GrandeAlphaLateOwnedRuntime {{ $script:LateStopped = $true }}
$Launch = [pscustomobject]@{{process_id=222}}
$Runtime = [pscustomobject]@{{
    state='OWNED'; detail='suspended recorded launcher'
    child_identity_valid=$true; child=$Launch
}}
Stop-GrandeAlphaLifecycle -Task $script:Task -Runtime $Runtime
if (-not $script:Stopped -or -not $script:LateStopped -or $script:Task.State -ne 'Ready') {{ exit 40 }}
"""
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows lifecycle preflight is Windows-only")
def test_absent_task_preflight_cleans_exact_orphan_and_rejects_any_live_unattributed_pid() -> None:
    manager = ROOT / "manage-shadow-schedule.ps1"
    command = rf"""
. '{manager}'
$script:RuntimeState = 'NONE'
$script:HeartbeatAlive = $true
$script:HeartbeatLiveness = 'STALE'
$script:Cleaned = $false
function Get-GrandeAlphaHeartbeatStatus {{
    [pscustomobject]@{{
        process_alive=$script:HeartbeatAlive
        liveness=$script:HeartbeatLiveness
        state='running'
        process_id=123
    }}
}}
function Resolve-GrandeAlphaRuntime {{
    [pscustomobject]@{{
        state=$script:RuntimeState
        child_identity_valid=($script:RuntimeState -eq 'ORPHANED')
        detail='test'
    }}
}}
function Stop-GrandeAlphaAbsentTaskRuntime {{ $script:Cleaned = $true }}
$Results = [ordered]@{{}}
try {{ [void](Invoke-GrandeAlphaAbsentTaskPreflight '-Remove'); $Results.stale = 'accepted' }}
catch {{ $Results.stale = 'rejected' }}
$script:HeartbeatLiveness = 'INVALID'
try {{ [void](Invoke-GrandeAlphaAbsentTaskPreflight '-Install'); $Results.invalid = 'accepted' }}
catch {{ $Results.invalid = 'rejected' }}
$script:StartTouched = $false
function Start-ScheduledTask {{ $script:StartTouched = $true }}
try {{ Start-GrandeAlphaLifecycle -Task ([pscustomobject]@{{State='Running'}}); $Results.start = 'accepted' }}
catch {{ $Results.start = 'rejected' }}
$Results.start_touched = $script:StartTouched
$script:RuntimeState = 'ORPHANED'
$Results.orphan = Invoke-GrandeAlphaAbsentTaskPreflight '-Remove'
$Results.cleaned = $script:Cleaned
$script:RuntimeState = 'STOPPED'; $script:HeartbeatAlive = $false
$Results.absent = Invoke-GrandeAlphaAbsentTaskPreflight '-Remove'
$Results | ConvertTo-Json -Compress
"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    preflight = json.loads(result.stdout)

    assert preflight == {
        "stale": "rejected",
        "invalid": "rejected",
        "start": "rejected",
        "start_touched": False,
        "orphan": True,
        "cleaned": True,
        "absent": False,
    }
    source = manager.read_text(encoding="utf-8")
    assert source.count("Invoke-GrandeAlphaAbsentTaskPreflight") >= 3
