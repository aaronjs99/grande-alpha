[CmdletBinding()]
param(
    [switch]$Install,
    [switch]$Start,
    [switch]$Stop,
    [switch]$Restart,
    [switch]$Status,
    [switch]$Remove,
    [switch]$Definition
)

$ErrorActionPreference = 'Stop'
function Test-FullyQualifiedPath([string]$Path) {
    if (-not [IO.Path]::IsPathRooted($Path)) {
        return $false
    }
    $Expanded = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    return [string]::Equals($Expanded, $Path.TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase)
}

$SelectedModes = @(
    $Install.IsPresent,
    $Start.IsPresent,
    $Stop.IsPresent,
    $Restart.IsPresent,
    $Status.IsPresent,
    $Remove.IsPresent,
    $Definition.IsPresent
) |
    Where-Object { $_ }
if ($SelectedModes.Count -gt 1) {
    throw 'Choose only one mode: -Install, -Start, -Stop, -Restart, -Status, -Remove, or -Definition.'
}
if ($SelectedModes.Count -eq 0) {
    $Status = $true
}

$TaskName = 'GRANDE Alpha Live Shadow'
$TaskPath = '\'
$ProjectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $MyInvocation.MyCommand.Path))
$Launcher = [IO.Path]::GetFullPath((Join-Path $ProjectRoot 'scheduled-shadow.ps1'))
$RuntimeHelper = [IO.Path]::GetFullPath((Join-Path $ProjectRoot 'runtime-path.ps1'))
$LifecycleHelper = [IO.Path]::GetFullPath((Join-Path $ProjectRoot 'shadow-lifecycle.ps1'))
$PowerShellExe = [IO.Path]::GetFullPath((Get-Command powershell.exe -ErrorAction Stop).Source)
$CurrentUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$CurrentUserSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$HostTimeZoneId = [TimeZoneInfo]::Local.Id
$HeartbeatPath = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'GRANDEAlpha\scheduled-shadow-heartbeat.json'))
$LifecyclePath = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'GRANDEAlpha\scheduled-shadow-lifecycle.json'))
$LocalTriggerTimes = [ordered]@{
    'Eastern Standard Time' = '09:20'
    'Central Standard Time' = '08:20'
    'Mountain Standard Time' = '07:20'
    # Arizona does not observe DST. This is 08:20 ET during Eastern standard
    # time and 09:20 ET during Eastern daylight time.
    'US Mountain Standard Time' = '06:20'
    'Pacific Standard Time' = '06:20'
}
$SupportedTimeZoneIds = @($LocalTriggerTimes.Keys)
$LocalTriggerTime = $LocalTriggerTimes[$HostTimeZoneId]

foreach ($Path in @($ProjectRoot, $Launcher, $RuntimeHelper, $LifecycleHelper, $PowerShellExe)) {
    if (-not (Test-FullyQualifiedPath $Path)) {
        throw "Scheduled-task paths must be absolute: $Path"
    }
}
if (-not (Test-Path -LiteralPath $ProjectRoot -PathType Container)) {
    throw "Project directory not found: $ProjectRoot"
}
if (-not (Test-Path -LiteralPath $Launcher -PathType Leaf)) {
    throw "Scheduled launcher not found: $Launcher"
}
if (-not (Test-Path -LiteralPath $RuntimeHelper -PathType Leaf)) {
    throw "Runtime helper not found: $RuntimeHelper"
}
if (-not (Test-Path -LiteralPath $LifecycleHelper -PathType Leaf)) {
    throw "Lifecycle helper not found: $LifecycleHelper"
}
if (-not (Test-Path -LiteralPath $PowerShellExe -PathType Leaf)) {
    throw "Windows PowerShell not found: $PowerShellExe"
}

. $RuntimeHelper
. $LifecycleHelper
$PythonExe = [IO.Path]::GetFullPath((Get-GrandeAlphaPython $ProjectRoot))
$PythonProcessExe = Get-GrandeAlphaPythonProcessExecutable $PythonExe

$ActionArguments = "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Launcher`""
$Spec = [ordered]@{
    task_name = $TaskName
    task_path = $TaskPath
    execute = $PowerShellExe
    arguments = $ActionArguments
    working_directory = $ProjectRoot
    local_time = $LocalTriggerTime
    host_time_zone_id = $HostTimeZoneId
    supported_time_zone_ids = $SupportedTimeZoneIds
    local_time_by_windows_zone = $LocalTriggerTimes
    target_eastern_time_window = '07:00-09:20'
    days_of_week = @('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')
    user_id = $CurrentUser
    logon_type = 'InteractiveToken'
    run_level = 'Limited'
    stores_password = $false
    start_when_available = $false
    wake_to_run = $true
    allow_start_if_on_batteries = $true
    stop_if_going_on_batteries = $false
    network_required = $false
    multiple_instances = 'IgnoreNew'
    execution_time_limit_hours = 0
    task_restart_count = 3
    task_restart_interval_minutes = 1
    application_mode = '--auto-shadow'
    supervisor_restart_initial_seconds = 15
    supervisor_restart_maximum_seconds = 300
    heartbeat_interval_seconds = 60
    heartbeat_stale_after_seconds = 180
    heartbeat_path = $HeartbeatPath
    lifecycle_path = $LifecyclePath
    lifecycle_schema_version = 1
    python_launcher = $PythonExe
    python_process_executable = $PythonProcessExe
    start_timeout_seconds = 45
    normal_close_timeout_seconds = 10
    stop_timeout_seconds = 30
    post_stop_reconcile_seconds = 15
}

if ($Definition) {
    $Spec | ConvertTo-Json -Depth 4
    exit 0
}

function Get-GrandeAlphaScheduledTask {
    return Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -ErrorAction SilentlyContinue
}

function Get-GrandeAlphaTaskContractMismatches($Task) {
    $Mismatches = [Collections.Generic.List[string]]::new()
    $Actions = @($Task.Actions)
    $Triggers = @($Task.Triggers)

    if (-not [string]::Equals([string]$Task.TaskName, $Spec.task_name, [StringComparison]::Ordinal)) {
        $Mismatches.Add("task name is '$($Task.TaskName)', expected '$($Spec.task_name)'")
    }
    if (-not [string]::Equals([string]$Task.TaskPath, $Spec.task_path, [StringComparison]::Ordinal)) {
        $Mismatches.Add("task path is '$($Task.TaskPath)', expected '$($Spec.task_path)'")
    }
    if ($Actions.Count -ne 1) {
        $Mismatches.Add("action count is $($Actions.Count), expected exactly 1")
    } else {
        $Action = $Actions[0]
        if (-not [string]::Equals([string]$Action.Execute, $Spec.execute, [StringComparison]::OrdinalIgnoreCase)) {
            $Mismatches.Add('action executable does not match the validated Windows PowerShell path')
        }
        if (-not [string]::Equals([string]$Action.Arguments, $Spec.arguments, [StringComparison]::Ordinal)) {
            $Mismatches.Add('action arguments do not match the --auto-shadow launcher contract')
        }
        if (-not [string]::Equals([string]$Action.WorkingDirectory, $Spec.working_directory, [StringComparison]::OrdinalIgnoreCase)) {
            $Mismatches.Add('action working directory does not match the validated project root')
        }
    }

    if ($Triggers.Count -ne 1) {
        $Mismatches.Add("trigger count is $($Triggers.Count), expected exactly 1")
    } else {
        $Trigger = $Triggers[0]
        try {
            $InstalledTime = ([datetimeoffset]::Parse([string]$Trigger.StartBoundary)).ToString('HH:mm')
        } catch {
            $InstalledTime = 'invalid'
        }
        if ($InstalledTime -ne $Spec.local_time) {
            $Mismatches.Add("local trigger is '$InstalledTime', expected '$($Spec.local_time)'")
        }
        if ([int]$Trigger.DaysOfWeek -ne 62 -or [int]$Trigger.WeeksInterval -ne 1) {
            $Mismatches.Add('trigger is not exactly weekly Monday-Friday')
        }
        if ($Trigger.Enabled -ne $true) {
            $Mismatches.Add('trigger is disabled')
        }
    }

    try {
        $InstalledSid = ([Security.Principal.NTAccount][string]$Task.Principal.UserId).
            Translate([Security.Principal.SecurityIdentifier]).Value
    } catch {
        $InstalledSid = 'unresolvable'
    }
    if ($InstalledSid -ne [Security.Principal.WindowsIdentity]::GetCurrent().User.Value) {
        $Mismatches.Add('principal is not the current interactive Windows user')
    }
    if ([string]$Task.Principal.LogonType -ne 'Interactive') {
        $Mismatches.Add('principal logon type is not InteractiveToken')
    }
    if ([string]$Task.Principal.RunLevel -ne 'Limited') {
        $Mismatches.Add('principal run level is not Limited')
    }

    $Settings = $Task.Settings
    if ($Settings.Enabled -ne $true) { $Mismatches.Add('task is disabled') }
    if ($Settings.StartWhenAvailable -ne $false) { $Mismatches.Add('late catch-up is enabled') }
    if ($Settings.WakeToRun -ne $true) { $Mismatches.Add('wake-to-run is disabled') }
    if ($Settings.DisallowStartIfOnBatteries -ne $false) { $Mismatches.Add('battery starts are disabled') }
    if ($Settings.StopIfGoingOnBatteries -ne $false) { $Mismatches.Add('task stops when switching to battery') }
    if ($Settings.RunOnlyIfNetworkAvailable -ne $false) { $Mismatches.Add('a Task Scheduler network condition is required') }
    if ([string]$Settings.MultipleInstances -ne 'IgnoreNew') { $Mismatches.Add('overlap policy is not IgnoreNew') }
    try {
        $ExecutionLimit = [Xml.XmlConvert]::ToTimeSpan([string]$Settings.ExecutionTimeLimit)
        if ($ExecutionLimit -ne [timespan]::Zero) {
            $Mismatches.Add('execution time limit is not unlimited')
        }
    } catch {
        $Mismatches.Add('execution time limit is invalid')
    }
    if ([int]$Settings.RestartCount -ne $Spec.task_restart_count) {
        $Mismatches.Add("task restart count is '$($Settings.RestartCount)', expected '$($Spec.task_restart_count)'")
    }
    try {
        $RestartInterval = [Xml.XmlConvert]::ToTimeSpan([string]$Settings.RestartInterval)
        if ($RestartInterval -ne [timespan]::FromMinutes($Spec.task_restart_interval_minutes)) {
            $Mismatches.Add('task restart interval is not one minute')
        }
    } catch {
        $Mismatches.Add('task restart interval is invalid')
    }

    return $Mismatches.ToArray()
}

function Get-GrandeAlphaTaskIdentityMismatches($Task) {
    $Mismatches = [Collections.Generic.List[string]]::new()
    $Actions = @($Task.Actions)
    if (-not [string]::Equals([string]$Task.TaskName, $Spec.task_name, [StringComparison]::Ordinal)) {
        $Mismatches.Add('task name does not match GRANDE Alpha')
    }
    if (-not [string]::Equals([string]$Task.TaskPath, $Spec.task_path, [StringComparison]::Ordinal)) {
        $Mismatches.Add('task path does not match GRANDE Alpha')
    }
    if ($Actions.Count -ne 1) {
        $Mismatches.Add('task does not have exactly one action')
    } else {
        $Action = $Actions[0]
        if (-not [string]::Equals([string]$Action.Execute, $Spec.execute, [StringComparison]::OrdinalIgnoreCase)) {
            $Mismatches.Add('action executable does not match the validated Windows PowerShell path')
        }
        if (-not [string]::Equals([string]$Action.Arguments, $Spec.arguments, [StringComparison]::Ordinal)) {
            $Mismatches.Add('action arguments do not match the exact scheduled launcher')
        }
        if (-not [string]::Equals([string]$Action.WorkingDirectory, $Spec.working_directory, [StringComparison]::OrdinalIgnoreCase)) {
            $Mismatches.Add('action working directory does not match this project root')
        }
    }
    try {
        $InstalledSid = ([Security.Principal.NTAccount][string]$Task.Principal.UserId).
            Translate([Security.Principal.SecurityIdentifier]).Value
    } catch {
        $InstalledSid = 'unresolvable'
    }
    if ($InstalledSid -ne $CurrentUserSid) {
        $Mismatches.Add('principal is not the current interactive Windows user')
    }
    if ([string]$Task.Principal.LogonType -ne 'Interactive') {
        $Mismatches.Add('principal logon type is not InteractiveToken')
    }
    if ([string]$Task.Principal.RunLevel -ne 'Limited') {
        $Mismatches.Add('principal run level is not Limited')
    }
    return $Mismatches.ToArray()
}

function Get-GrandeAlphaStrictHeartbeatProcessId($Heartbeat) {
    $Property = $Heartbeat.PSObject.Properties['process_id']
    if ($null -eq $Property) {
        return $null
    }
    $Value = $Property.Value
    $IntegralType = (
        $Value -is [byte] -or
        $Value -is [sbyte] -or
        $Value -is [int16] -or
        $Value -is [uint16] -or
        $Value -is [int32] -or
        $Value -is [uint32] -or
        $Value -is [int64] -or
        $Value -is [uint64]
    )
    if (-not $IntegralType) {
        return $null
    }
    try {
        $Candidate = [int64]$Value
        if ($Candidate -le 0 -or $Candidate -gt [int]::MaxValue) {
            return $null
        }
        return [int]$Candidate
    } catch {
        return $null
    }
}

function Test-GrandeAlphaHeartbeatPidEvidenceSafe($HeartbeatStatus) {
    return (
        -not $HeartbeatStatus.heartbeat_present -or
        $HeartbeatStatus.process_id_valid -eq $true
    )
}

function Get-GrandeAlphaHeartbeatStatus {
    $Result = [ordered]@{
        heartbeat_present = $false
        process_id_valid = $false
        liveness = 'MISSING'
        operational_state = 'DEGRADED'
        observed_at_utc = $null
        age_seconds = $null
        process_id = $null
        process_alive = $false
        state = $null
        connected = $false
        shadow_running = $false
        session_open = $false
        last_refresh_utc = $null
        last_refresh_age_seconds = $null
        last_reconcile_at_utc = $null
        last_reconcile_age_seconds = $null
        shadow_equity = $null
        shadow_pnl = $null
        shadow_fills = $null
        detail = 'No auto-shadow heartbeat has been recorded yet.'
    }
    if (-not (Test-Path -LiteralPath $HeartbeatPath -PathType Leaf)) {
        return [pscustomobject]$Result
    }
    $Result.heartbeat_present = $true

    try {
        $Heartbeat = Get-Content -LiteralPath $HeartbeatPath -Raw -Encoding utf8 | ConvertFrom-Json
        # Preserve exact live-PID evidence even if any timestamp or runtime field
        # below is malformed. Lifecycle mutation guards consume these fields on
        # every INVALID return path.
        $ProcessId = Get-GrandeAlphaStrictHeartbeatProcessId $Heartbeat
        if ($null -ne $ProcessId) {
            $Result.process_id_valid = $true
            $Result.process_id = [int]$ProcessId
            $Result.process_alive = (
                $null -ne (Get-Process -Id ([int]$ProcessId) -ErrorAction SilentlyContinue)
            )
        }
        $ObservedAt = [datetimeoffset]::Parse(
            [string]$Heartbeat.observed_at_utc,
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::RoundtripKind
        ).ToUniversalTime()
        $AgeSeconds = [int][Math]::Floor(([datetimeoffset]::UtcNow - $ObservedAt).TotalSeconds)
        $ProcessAlive = $Result.process_alive
        $RuntimeFields = @(
            'connected',
            'shadow_running',
            'session_open',
            'last_refresh_utc',
            'last_reconcile_at_utc',
            'shadow_equity',
            'shadow_pnl',
            'shadow_fills'
        )
        $RuntimeFieldNames = @($Heartbeat.runtime.PSObject.Properties.Name)
        $ContractValid = (
            [int]$Heartbeat.schema_version -eq 1 -and
            $Result.process_id_valid -eq $true -and
            [string]$Heartbeat.mode -eq '--auto-shadow' -and
            [string]$Heartbeat.liveness_source -eq 'qt_event_loop_timer' -and
            $Heartbeat.read_only -eq $true -and
            $Heartbeat.broker_writes -eq $false -and
            $Heartbeat.live_authority -eq $false -and
            $null -ne $Heartbeat.runtime -and
            @($RuntimeFields | Where-Object { $_ -notin $RuntimeFieldNames }).Count -eq 0
        )

        $Connected = $Heartbeat.runtime.connected -eq $true
        $ShadowRunning = $Heartbeat.runtime.shadow_running -eq $true
        $SessionOpen = $Heartbeat.runtime.session_open -eq $true
        $ShadowEquity = [double]$Heartbeat.runtime.shadow_equity
        $ShadowPnl = [double]$Heartbeat.runtime.shadow_pnl
        $ShadowFills = [int]$Heartbeat.runtime.shadow_fills
        $RuntimeValuesValid = (
            -not [double]::IsNaN($ShadowEquity) -and
            -not [double]::IsInfinity($ShadowEquity) -and
            -not [double]::IsNaN($ShadowPnl) -and
            -not [double]::IsInfinity($ShadowPnl) -and
            $ShadowFills -ge 0
        )

        $LastRefresh = $null
        $LastRefreshAgeSeconds = $null
        if (-not [string]::IsNullOrWhiteSpace([string]$Heartbeat.runtime.last_refresh_utc)) {
            $LastRefresh = [datetimeoffset]::Parse(
                [string]$Heartbeat.runtime.last_refresh_utc,
                [Globalization.CultureInfo]::InvariantCulture,
                [Globalization.DateTimeStyles]::RoundtripKind
            ).ToUniversalTime()
            $LastRefreshAgeSeconds = [int][Math]::Floor(
                ([datetimeoffset]::UtcNow - $LastRefresh).TotalSeconds
            )
        }
        $LastReconcile = $null
        $LastReconcileAgeSeconds = $null
        if (-not [string]::IsNullOrWhiteSpace([string]$Heartbeat.runtime.last_reconcile_at_utc)) {
            $LastReconcile = [datetimeoffset]::Parse(
                [string]$Heartbeat.runtime.last_reconcile_at_utc,
                [Globalization.CultureInfo]::InvariantCulture,
                [Globalization.DateTimeStyles]::RoundtripKind
            ).ToUniversalTime()
            $LastReconcileAgeSeconds = [int][Math]::Floor(
                ([datetimeoffset]::UtcNow - $LastReconcile).TotalSeconds
            )
        }

        $Result.observed_at_utc = $ObservedAt.ToString('o')
        $Result.age_seconds = $AgeSeconds
        $Result.state = [string]$Heartbeat.state
        $Result.connected = $Connected
        $Result.shadow_running = $ShadowRunning
        $Result.session_open = $SessionOpen
        $Result.last_refresh_utc = if ($null -eq $LastRefresh) { $null } else { $LastRefresh.ToString('o') }
        $Result.last_refresh_age_seconds = $LastRefreshAgeSeconds
        $Result.last_reconcile_at_utc = if ($null -eq $LastReconcile) { $null } else { $LastReconcile.ToString('o') }
        $Result.last_reconcile_age_seconds = $LastReconcileAgeSeconds
        $Result.shadow_equity = $ShadowEquity
        $Result.shadow_pnl = $ShadowPnl
        $Result.shadow_fills = $ShadowFills
        if (-not $ContractValid -or -not $RuntimeValuesValid) {
            $Result.liveness = 'INVALID'
            $Result.detail = 'Heartbeat read-only contract fields are invalid.'
        } elseif ($AgeSeconds -lt -5) {
            $Result.liveness = 'INVALID'
            $Result.detail = 'Heartbeat timestamp is unexpectedly in the future.'
        } elseif ([string]$Heartbeat.state -ne 'running') {
            $Result.liveness = 'STOPPED'
            $Result.detail = "Application heartbeat state is '$($Heartbeat.state)'."
        } elseif (-not $ProcessAlive) {
            $Result.liveness = 'DEAD_PROCESS'
            $Result.detail = "Heartbeat process $ProcessId is not running."
        } elseif ($AgeSeconds -gt $Spec.heartbeat_stale_after_seconds) {
            $Result.liveness = 'STALE'
            $Result.detail = "Heartbeat is older than $($Spec.heartbeat_stale_after_seconds) seconds."
        } else {
            $Result.liveness = 'EVENT_LOOP_FRESH'
            if (-not $SessionOpen) {
                $Result.operational_state = 'WAITING'
                $Result.detail = (
                    'Event-loop liveness is fresh and the equity session is not open; ' +
                    'broker/shadow inactivity is expected.'
                )
            } elseif (-not $Connected -or -not $ShadowRunning) {
                $Result.operational_state = 'DEGRADED'
                $Result.detail = (
                    'The equity session is open, but the broker connection or shadow session ' +
                    'is not active.'
                )
            } elseif (
                $null -eq $LastRefreshAgeSeconds -or
                $null -eq $LastReconcileAgeSeconds -or
                $LastRefreshAgeSeconds -lt -5 -or
                $LastReconcileAgeSeconds -lt -5 -or
                $LastRefreshAgeSeconds -gt $Spec.heartbeat_stale_after_seconds -or
                $LastReconcileAgeSeconds -gt $Spec.heartbeat_stale_after_seconds
            ) {
                $Result.operational_state = 'DEGRADED'
                $Result.detail = (
                    'Event-loop liveness is fresh, but active shadow refresh or reconcile data is stale.'
                )
            } else {
                $Result.operational_state = 'ACTIVE'
                $Result.detail = 'Shadow is active with current refresh and reconcile clocks.'
            }
        }
    } catch {
        $Result.liveness = 'INVALID'
        $Result.detail = "Heartbeat could not be parsed: $($_.Exception.Message)"
    }
    return [pscustomobject]$Result
}

function Get-GrandeAlphaInstalledRuntime {
    return Get-GrandeAlphaOwnedRuntime `
        -LifecyclePath $LifecyclePath `
        -ProjectRoot $ProjectRoot `
        -Launcher $Launcher `
        -PythonLauncher $PythonExe `
        -PythonProcessExecutable $PythonProcessExe `
        -PowerShellExe $PowerShellExe `
        -ActionArguments $ActionArguments `
        -CurrentUserSid $CurrentUserSid `
        -TaskName $TaskName
}

function Test-GrandeAlphaCurrentProcessMatches {
    param(
        [Parameter(Mandatory = $true)]$Snapshot,
        [Parameter(Mandatory = $true)][string]$ExpectedExecutable,
        [Parameter(Mandatory = $true)][string]$ExpectedArguments
    )
    return Test-GrandeAlphaProcessIdentity `
        -Snapshot $Snapshot `
        -ExpectedProcessId ([int]$Snapshot.process_id) `
        -ExpectedStartedAtUtc ([string]$Snapshot.started_at_utc) `
        -ExpectedExecutable $ExpectedExecutable `
        -ExpectedArguments $ExpectedArguments `
        -ExpectedOwnerSid $CurrentUserSid
}

function Get-GrandeAlphaLegacyRuntime($HeartbeatStatus) {
    $Result = [ordered]@{
        state = 'NONE'
        detail = 'No safely attributable legacy foreground child was found.'
        record = $null
        wrapper = $null
        child = $null
        wrapper_identity_valid = $false
        child_identity_valid = $false
    }
    if (
        $HeartbeatStatus.liveness -notin @('EVENT_LOOP_FRESH', 'STALE') -or
        [string]$HeartbeatStatus.state -ne 'running' -or
        -not $HeartbeatStatus.process_alive -or
        [int]$HeartbeatStatus.process_id -le 0
    ) {
        return [pscustomobject]$Result
    }

    $Application = Get-GrandeAlphaProcessSnapshot ([int]$HeartbeatStatus.process_id)
    if ($null -eq $Application) {
        return [pscustomobject]$Result
    }
    if (-not (Test-GrandeAlphaCurrentProcessMatches `
        -Snapshot $Application `
        -ExpectedExecutable $PythonProcessExe `
        -ExpectedArguments '-m grande_alpha.app --auto-shadow'
    )) {
        $Result.state = 'UNVERIFIED'
        $Result.detail = 'Fresh heartbeat PID does not match the exact managed Python and --auto-shadow argv.'
        return [pscustomobject]$Result
    }

    $ApplicationParent = Get-GrandeAlphaProcessSnapshot ([int]$Application.parent_process_id)
    if ($null -eq $ApplicationParent) {
        $Result.state = 'UNVERIFIED'
        $Result.detail = 'Fresh legacy application process has no attributable parent.'
        return [pscustomobject]$Result
    }
    if (Test-GrandeAlphaCurrentProcessMatches `
        -Snapshot $ApplicationParent `
        -ExpectedExecutable $PythonExe `
        -ExpectedArguments '-m grande_alpha.app --auto-shadow'
    ) {
        $LaunchProcess = $ApplicationParent
        $Wrapper = Get-GrandeAlphaProcessSnapshot ([int]$LaunchProcess.parent_process_id)
    } elseif (Test-GrandeAlphaCurrentProcessMatches `
        -Snapshot $ApplicationParent `
        -ExpectedExecutable $PowerShellExe `
        -ExpectedArguments $ActionArguments
    ) {
        # A non-venv Python can be both the launched process and heartbeat process.
        $LaunchProcess = $Application
        $Wrapper = $ApplicationParent
    } else {
        $Result.state = 'UNVERIFIED'
        $Result.detail = 'Fresh legacy application parent is neither the managed Python launcher nor the scheduled wrapper.'
        return [pscustomobject]$Result
    }
    if ($null -eq $Wrapper) {
        $Result.state = 'UNVERIFIED'
        $Result.detail = 'Fresh legacy child is alive, but its original wrapper is no longer available for attribution.'
        return [pscustomobject]$Result
    }
    $WrapperValid = Test-GrandeAlphaCurrentProcessMatches `
        -Snapshot $Wrapper `
        -ExpectedExecutable $PowerShellExe `
        -ExpectedArguments $ActionArguments
    try {
        $ChildStarted = [datetimeoffset]::Parse([string]$Application.started_at_utc).ToUniversalTime()
        $LaunchStarted = [datetimeoffset]::Parse([string]$LaunchProcess.started_at_utc).ToUniversalTime()
        $WrapperStarted = [datetimeoffset]::Parse([string]$Wrapper.started_at_utc).ToUniversalTime()
        $StartOrderValid = $ChildStarted -ge $LaunchStarted -and $LaunchStarted -ge $WrapperStarted
    } catch {
        $StartOrderValid = $false
    }
    if (-not $WrapperValid -or -not $StartOrderValid) {
        $Result.state = 'UNVERIFIED'
        $Result.detail = 'Fresh legacy child does not have the exact scheduled launcher as its current-user parent.'
        return [pscustomobject]$Result
    }

    $Result.state = 'LEGACY_OWNED'
    $Result.detail = 'Exact foreground child and scheduled-launcher parent match; restart once to create the durable lifecycle manifest.'
    $Result.wrapper = $Wrapper
    $Result.child = $LaunchProcess
    $Result.wrapper_identity_valid = $true
    $Result.child_identity_valid = $true
    return [pscustomobject]$Result
}

function Get-GrandeAlphaApplicationFromOwnedLauncher($Runtime) {
    $Result = [ordered]@{
        state = 'NONE'
        detail = 'No exact application process is present under the owned Python launcher.'
        process = $null
        identity_valid = $false
    }
    if (
        $Runtime.state -notin @('OWNED', 'ORPHANED', 'LEGACY_OWNED') -or
        -not $Runtime.child_identity_valid
    ) {
        return [pscustomobject]$Result
    }
    $Candidates = if ([string]::Equals(
        $PythonExe,
        $PythonProcessExe,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        @($Runtime.child)
    } else {
        @(Get-GrandeAlphaDirectChildProcessSnapshots ([int]$Runtime.child.process_id))
    }
    $Matches = [Collections.Generic.List[object]]::new()
    foreach ($Candidate in $Candidates) {
        if (-not (Test-GrandeAlphaCurrentProcessMatches `
            -Snapshot $Candidate `
            -ExpectedExecutable $PythonProcessExe `
            -ExpectedArguments '-m grande_alpha.app --auto-shadow'
        )) {
            continue
        }
        try {
            $ApplicationStarted = [datetimeoffset]::Parse([string]$Candidate.started_at_utc).ToUniversalTime()
            $LaunchStarted = [datetimeoffset]::Parse([string]$Runtime.child.started_at_utc).ToUniversalTime()
            if ($ApplicationStarted -lt $LaunchStarted) {
                continue
            }
        } catch {
            continue
        }
        $Matches.Add($Candidate)
    }
    if ($Matches.Count -eq 1) {
        $Result.state = 'VERIFIED'
        $Result.detail = 'Exact application identity is the owned launcher or its sole matching direct child.'
        $Result.process = $Matches[0]
        $Result.identity_valid = $true
    } elseif ($Matches.Count -gt 1) {
        $Result.state = 'UNVERIFIED'
        $Result.detail = 'More than one direct child matches the application identity; no process is attributable.'
    }
    return [pscustomobject]$Result
}

function Get-GrandeAlphaApplicationRuntime {
    param(
        [Parameter(Mandatory = $true)]$Runtime,
        [Parameter(Mandatory = $true)]$HeartbeatStatus
    )
    $Result = [ordered]@{
        state = 'NONE'
        detail = 'No fresh application heartbeat is available.'
        process = $null
        identity_valid = $false
    }
    if (
        $Runtime.state -notin @('OWNED', 'ORPHANED', 'LEGACY_OWNED') -or
        -not $Runtime.child_identity_valid
    ) {
        return [pscustomobject]$Result
    }
    if (
        $HeartbeatStatus.liveness -notin @('EVENT_LOOP_FRESH', 'STALE') -or
        [string]$HeartbeatStatus.state -ne 'running' -or
        -not $HeartbeatStatus.process_alive
    ) {
        return Get-GrandeAlphaApplicationFromOwnedLauncher $Runtime
    }
    $Application = Get-GrandeAlphaProcessSnapshot ([int]$HeartbeatStatus.process_id)
    if ($null -eq $Application -or -not (Test-GrandeAlphaCurrentProcessMatches `
        -Snapshot $Application `
        -ExpectedExecutable $PythonProcessExe `
        -ExpectedArguments '-m grande_alpha.app --auto-shadow'
    )) {
        $Result.state = 'UNVERIFIED'
        $Result.detail = 'Heartbeat process does not match the exact Python application identity.'
        return [pscustomobject]$Result
    }
    $TreeValid = (
        [int]$Application.process_id -eq [int]$Runtime.child.process_id -or
        [int]$Application.parent_process_id -eq [int]$Runtime.child.process_id
    )
    try {
        $ApplicationStarted = [datetimeoffset]::Parse([string]$Application.started_at_utc).ToUniversalTime()
        $LaunchStarted = [datetimeoffset]::Parse([string]$Runtime.child.started_at_utc).ToUniversalTime()
        $TreeValid = $TreeValid -and $ApplicationStarted -ge $LaunchStarted
    } catch {
        $TreeValid = $false
    }
    if (-not $TreeValid) {
        $Result.state = 'UNVERIFIED'
        $Result.detail = 'Heartbeat process is not the recorded launcher or its direct child.'
        return [pscustomobject]$Result
    }
    $Result.state = 'VERIFIED'
    $Result.detail = 'Fresh heartbeat process matches the exact owned launcher process tree.'
    $Result.process = $Application
    $Result.identity_valid = $true
    return [pscustomobject]$Result
}

function Test-GrandeAlphaHeartbeatPidAttributed {
    param(
        [Parameter(Mandatory = $true)]$HeartbeatStatus,
        [Parameter(Mandatory = $true)]$ApplicationRuntime
    )
    if (-not $HeartbeatStatus.process_alive) {
        return $true
    }
    return (
        $ApplicationRuntime.state -eq 'VERIFIED' -and
        [int]$HeartbeatStatus.process_id -eq [int]$ApplicationRuntime.process.process_id
    )
}

function Resolve-GrandeAlphaRuntime($HeartbeatStatus) {
    $Runtime = Get-GrandeAlphaInstalledRuntime
    if ($Runtime.state -eq 'NONE') {
        $Legacy = Get-GrandeAlphaLegacyRuntime $HeartbeatStatus
        if ($Legacy.state -ne 'NONE') {
            return $Legacy
        }
    }
    return $Runtime
}

function Assert-GrandeAlphaTaskContract {
    $Task = Get-GrandeAlphaScheduledTask
    if ($null -eq $Task) {
        throw 'GRANDE Alpha scheduled live shadow is not installed. Run -Install first.'
    }
    $Mismatches = @(Get-GrandeAlphaTaskContractMismatches $Task)
    if ($Mismatches.Count -gt 0) {
        throw "The installed task contract is invalid; no lifecycle action was taken: $($Mismatches -join '; ')"
    }
    return $Task
}

function Test-GrandeAlphaSnapshotStillExact($Snapshot) {
    if ($null -eq $Snapshot) {
        return $false
    }
    $Current = Get-GrandeAlphaProcessSnapshot ([int]$Snapshot.process_id)
    if ($null -eq $Current) {
        return $false
    }
    return Test-GrandeAlphaProcessIdentity `
        -Snapshot $Current `
        -ExpectedProcessId ([int]$Snapshot.process_id) `
        -ExpectedStartedAtUtc ([string]$Snapshot.started_at_utc) `
        -ExpectedExecutable $PythonProcessExe `
        -ExpectedArguments '-m grande_alpha.app --auto-shadow' `
        -ExpectedOwnerSid $CurrentUserSid
}

function Test-GrandeAlphaLaunchSnapshotStillExact($Snapshot) {
    if ($null -eq $Snapshot) {
        return $false
    }
    $Current = Get-GrandeAlphaProcessSnapshot ([int]$Snapshot.process_id)
    if ($null -eq $Current) {
        return $false
    }
    return Test-GrandeAlphaProcessIdentity `
        -Snapshot $Current `
        -ExpectedProcessId ([int]$Snapshot.process_id) `
        -ExpectedStartedAtUtc ([string]$Snapshot.started_at_utc) `
        -ExpectedExecutable $PythonExe `
        -ExpectedArguments '-m grande_alpha.app --auto-shadow' `
        -ExpectedOwnerSid $CurrentUserSid
}

function Test-GrandeAlphaWrapperSnapshotStillExact($Snapshot) {
    if ($null -eq $Snapshot) {
        return $false
    }
    $Current = Get-GrandeAlphaProcessSnapshot ([int]$Snapshot.process_id)
    if ($null -eq $Current) {
        return $false
    }
    return Test-GrandeAlphaProcessIdentity `
        -Snapshot $Current `
        -ExpectedProcessId ([int]$Snapshot.process_id) `
        -ExpectedStartedAtUtc ([string]$Snapshot.started_at_utc) `
        -ExpectedExecutable $PowerShellExe `
        -ExpectedArguments $ActionArguments `
        -ExpectedOwnerSid $CurrentUserSid
}

function Wait-GrandeAlphaProcessExit {
    param(
        [Parameter(Mandatory = $true)]$Snapshot,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds
    )
    $Deadline = [datetimeoffset]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([datetimeoffset]::UtcNow -lt $Deadline) {
        if (-not (Test-GrandeAlphaSnapshotStillExact $Snapshot)) {
            return $true
        }
        Start-Sleep -Milliseconds 250
    }
    return -not (Test-GrandeAlphaSnapshotStillExact $Snapshot)
}

function Wait-GrandeAlphaLaunchProcessExit {
    param(
        [Parameter(Mandatory = $true)]$Snapshot,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds
    )
    $Deadline = [datetimeoffset]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([datetimeoffset]::UtcNow -lt $Deadline) {
        if (-not (Test-GrandeAlphaLaunchSnapshotStillExact $Snapshot)) {
            return $true
        }
        Start-Sleep -Milliseconds 250
    }
    return -not (Test-GrandeAlphaLaunchSnapshotStillExact $Snapshot)
}

function Wait-GrandeAlphaWrapperProcessExit {
    param(
        [Parameter(Mandatory = $true)]$Snapshot,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds
    )
    $Deadline = [datetimeoffset]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([datetimeoffset]::UtcNow -lt $Deadline) {
        if (-not (Test-GrandeAlphaWrapperSnapshotStillExact $Snapshot)) {
            return $true
        }
        Start-Sleep -Milliseconds 250
    }
    return -not (Test-GrandeAlphaWrapperSnapshotStillExact $Snapshot)
}

function Stop-GrandeAlphaLateOwnedRuntime {
    param(
        [Parameter(Mandatory = $true)]$Runtime,
        [Parameter(Mandatory = $true)]$HeartbeatStatus
    )
    if (
        $Runtime.state -notin @('OWNED', 'ORPHANED') -or
        -not $Runtime.child_identity_valid
    ) {
        throw 'Late runtime is not an exact manifest-owned process tree.'
    }
    if (-not (Test-GrandeAlphaHeartbeatPidEvidenceSafe $HeartbeatStatus)) {
        throw 'Late-runtime cleanup is blocked by a heartbeat with a malformed process ID.'
    }
    $ApplicationRuntime = Get-GrandeAlphaApplicationRuntime `
        -Runtime $Runtime `
        -HeartbeatStatus $HeartbeatStatus
    if ($ApplicationRuntime.state -eq 'UNVERIFIED') {
        throw "Late owned launcher has an ambiguous application identity: $($ApplicationRuntime.detail)"
    }
    if (-not (Test-GrandeAlphaHeartbeatPidAttributed $HeartbeatStatus $ApplicationRuntime)) {
        throw 'Late heartbeat PID is alive but does not match the exact owned application.'
    }
    $ApplicationSnapshot = if ($ApplicationRuntime.state -eq 'VERIFIED') {
        $ApplicationRuntime.process
    } else {
        $null
    }
    $LaunchSnapshot = $Runtime.child

    if ($null -ne $ApplicationSnapshot) {
        $CloseRequested = $false
        try {
            $CloseRequested = $ApplicationSnapshot.native_process.CloseMainWindow()
        } catch {
            $CloseRequested = $false
        }
        if ($CloseRequested) {
            [void](Wait-GrandeAlphaProcessExit -Snapshot $ApplicationSnapshot -TimeoutSeconds 2)
        }
        if (Test-GrandeAlphaSnapshotStillExact $ApplicationSnapshot) {
            $ExactApplication = Get-GrandeAlphaProcessSnapshot ([int]$ApplicationSnapshot.process_id)
            $ExactApplication.native_process.Kill()
            if (-not (Wait-GrandeAlphaProcessExit -Snapshot $ApplicationSnapshot -TimeoutSeconds 5)) {
                throw "Late exact application PID $($ApplicationSnapshot.process_id) did not exit."
            }
        }
    }
    if (
        (
            $null -eq $ApplicationSnapshot -or
            [int]$LaunchSnapshot.process_id -ne [int]$ApplicationSnapshot.process_id
        ) -and
        -not (Wait-GrandeAlphaLaunchProcessExit -Snapshot $LaunchSnapshot -TimeoutSeconds 5)
    ) {
        if (-not (Test-GrandeAlphaLaunchSnapshotStillExact $LaunchSnapshot)) {
            throw 'Late Python launcher identity changed; no launcher termination was attempted.'
        }
        $ExactLauncher = Get-GrandeAlphaProcessSnapshot ([int]$LaunchSnapshot.process_id)
        $ExactLauncher.native_process.Kill()
        if (-not (Wait-GrandeAlphaLaunchProcessExit -Snapshot $LaunchSnapshot -TimeoutSeconds 5)) {
            throw "Late exact Python launcher PID $($LaunchSnapshot.process_id) did not exit."
        }
    }
    Write-Host (
        'Stopped a newly proven exact child that appeared while Task Scheduler was stopping the wrapper.'
    ) -ForegroundColor Yellow
}

function Stop-GrandeAlphaAbsentTaskRuntime {
    param(
        [Parameter(Mandatory = $true)]$Runtime,
        [Parameter(Mandatory = $true)]$HeartbeatStatus
    )
    if (-not (Test-GrandeAlphaHeartbeatPidEvidenceSafe $HeartbeatStatus)) {
        throw 'Absent-task cleanup is blocked by a heartbeat with a malformed process ID.'
    }
    if ($Runtime.state -in @('INVALID_RECORD', 'UNVERIFIED')) {
        throw "Absent-task runtime ownership is $($Runtime.state); no process was changed."
    }
    if ($Runtime.state -notin @(
        'OWNED',
        'ORPHANED',
        'LEGACY_OWNED',
        'STARTING',
        'RETRYING'
    )) {
        if ($HeartbeatStatus.process_alive) {
            throw 'The task is absent, but a heartbeat PID is alive without exact lifecycle ownership.'
        }
        return
    }

    $ApplicationRuntime = Get-GrandeAlphaApplicationRuntime `
        -Runtime $Runtime `
        -HeartbeatStatus $HeartbeatStatus
    $LaunchSnapshot = if (
        $Runtime.state -in @('OWNED', 'ORPHANED', 'LEGACY_OWNED') -and
        $Runtime.child_identity_valid
    ) {
        $Runtime.child
    } else {
        $null
    }
    if ($null -ne $LaunchSnapshot -and $ApplicationRuntime.state -eq 'UNVERIFIED') {
        throw (
            'The task is absent and its launcher is exact, but the application process is not ' +
            "unambiguously attributable: $($ApplicationRuntime.detail)"
        )
    }
    if (-not (Test-GrandeAlphaHeartbeatPidAttributed $HeartbeatStatus $ApplicationRuntime)) {
        throw 'Absent-task heartbeat PID is alive but does not match the exact owned application.'
    }

    $WrapperSnapshot = if ($Runtime.wrapper_identity_valid) { $Runtime.wrapper } else { $null }
    if ($null -ne $WrapperSnapshot -and (Test-GrandeAlphaWrapperSnapshotStillExact $WrapperSnapshot)) {
        $ExactWrapper = Get-GrandeAlphaProcessSnapshot ([int]$WrapperSnapshot.process_id)
        $ExactWrapper.native_process.Kill()
        if (-not (Wait-GrandeAlphaWrapperProcessExit -Snapshot $WrapperSnapshot -TimeoutSeconds 5)) {
            throw "Exact orphaned wrapper PID $($WrapperSnapshot.process_id) did not exit."
        }
    }

    if ($ApplicationRuntime.state -eq 'VERIFIED') {
        $ApplicationSnapshot = $ApplicationRuntime.process
        $CloseRequested = $false
        try {
            $CloseRequested = $ApplicationSnapshot.native_process.CloseMainWindow()
        } catch {
            $CloseRequested = $false
        }
        if ($CloseRequested) {
            [void](Wait-GrandeAlphaProcessExit -Snapshot $ApplicationSnapshot -TimeoutSeconds 2)
        }
        if (Test-GrandeAlphaSnapshotStillExact $ApplicationSnapshot) {
            $ExactApplication = Get-GrandeAlphaProcessSnapshot ([int]$ApplicationSnapshot.process_id)
            $ExactApplication.native_process.Kill()
            if (-not (Wait-GrandeAlphaProcessExit -Snapshot $ApplicationSnapshot -TimeoutSeconds 5)) {
                throw "Exact orphaned application PID $($ApplicationSnapshot.process_id) did not exit."
            }
        }
    } else {
        $ApplicationSnapshot = $null
    }

    if (
        $null -ne $LaunchSnapshot -and
        [int]$LaunchSnapshot.process_id -ne [int]$ApplicationSnapshot.process_id -and
        -not (Wait-GrandeAlphaLaunchProcessExit -Snapshot $LaunchSnapshot -TimeoutSeconds 5)
    ) {
        if (-not (Test-GrandeAlphaLaunchSnapshotStillExact $LaunchSnapshot)) {
            throw 'Orphaned Python launcher identity changed; no launcher termination was attempted.'
        }
        $ExactLauncher = Get-GrandeAlphaProcessSnapshot ([int]$LaunchSnapshot.process_id)
        $ExactLauncher.native_process.Kill()
        if (-not (Wait-GrandeAlphaLaunchProcessExit -Snapshot $LaunchSnapshot -TimeoutSeconds 5)) {
            throw "Exact orphaned Python launcher PID $($LaunchSnapshot.process_id) did not exit."
        }
    }

    $ReconcileDeadline = [datetimeoffset]::UtcNow.AddSeconds($Spec.post_stop_reconcile_seconds)
    $StableOffObservations = 0
    while ([datetimeoffset]::UtcNow -lt $ReconcileDeadline) {
        $PostHeartbeat = Get-GrandeAlphaHeartbeatStatus
        if (-not (Test-GrandeAlphaHeartbeatPidEvidenceSafe $PostHeartbeat)) {
            throw 'Absent-task post-stop heartbeat has a malformed process ID.'
        }
        $PostRuntime = Get-GrandeAlphaInstalledRuntime
        if ($PostRuntime.state -in @('OWNED', 'ORPHANED')) {
            $LateApplication = Get-GrandeAlphaApplicationRuntime `
                -Runtime $PostRuntime `
                -HeartbeatStatus $PostHeartbeat
            if ($LateApplication.state -in @('VERIFIED', 'NONE')) {
                Stop-GrandeAlphaLateOwnedRuntime `
                    -Runtime $PostRuntime `
                    -HeartbeatStatus $PostHeartbeat
                $StableOffObservations = 0
                continue
            }
            throw "Absent-task late application identity is $($LateApplication.state)."
        } elseif ($PostRuntime.state -in @('INVALID_RECORD', 'UNVERIFIED')) {
            throw "Absent-task post-stop ownership became $($PostRuntime.state)."
        } elseif (
            $PostRuntime.state -in @('STOPPED', 'NONE') -and
            -not $PostHeartbeat.process_alive
        ) {
            $StableOffObservations++
            if ($StableOffObservations -ge 2) {
                return
            }
        } else {
            $StableOffObservations = 0
        }
        Start-Sleep -Milliseconds 250
    }
    throw 'Absent-task cleanup did not prove two stable observations with no owned or heartbeat process.'
}

function Invoke-GrandeAlphaAbsentTaskPreflight([string]$Operation) {
    $HeartbeatStatus = Get-GrandeAlphaHeartbeatStatus
    if (-not (Test-GrandeAlphaHeartbeatPidEvidenceSafe $HeartbeatStatus)) {
        throw (
            "$Operation is blocked because the heartbeat has a malformed process ID; " +
            'no task mutation was attempted.'
        )
    }
    $Runtime = Resolve-GrandeAlphaRuntime $HeartbeatStatus
    if ($Runtime.state -in @(
        'OWNED',
        'ORPHANED',
        'LEGACY_OWNED',
        'STARTING',
        'RETRYING'
    )) {
        Stop-GrandeAlphaAbsentTaskRuntime `
            -Runtime $Runtime `
            -HeartbeatStatus $HeartbeatStatus
        return $true
    }
    if ($Runtime.state -in @('INVALID_RECORD', 'UNVERIFIED') -or $HeartbeatStatus.process_alive) {
        throw (
            "$Operation is blocked because an absent-task lifecycle or heartbeat points to a " +
            'live or unattributed runtime; no task mutation was attempted.'
        )
    }
    return $false
}

function Stop-GrandeAlphaLifecycleForTaskMutation {
    param(
        [Parameter(Mandatory = $true)]$Task,
        [Parameter(Mandatory = $true)]$Runtime,
        [Parameter(Mandatory = $true)]$HeartbeatStatus,
        [Parameter(Mandatory = $true)][string]$Operation
    )

    if ([string]$Task.State -ne 'Disabled') {
        Stop-GrandeAlphaLifecycle -Task $Task -Runtime $Runtime
        return
    }
    if (-not (Test-GrandeAlphaHeartbeatPidEvidenceSafe $HeartbeatStatus)) {
        throw "$Operation is blocked because the disabled task has a malformed heartbeat PID."
    }
    if ($Runtime.state -in @('INVALID_RECORD', 'UNVERIFIED')) {
        throw "$Operation is blocked because disabled-task runtime ownership is $($Runtime.state)."
    }
    if ($Runtime.state -in @(
        'OWNED',
        'ORPHANED',
        'LEGACY_OWNED',
        'STARTING',
        'RETRYING'
    )) {
        # A Disabled task cannot queue a new instance. Reuse the exact orphan
        # cleanup path, then independently prove terminal state before mutation.
        Stop-GrandeAlphaAbsentTaskRuntime `
            -Runtime $Runtime `
            -HeartbeatStatus $HeartbeatStatus
    } elseif ($HeartbeatStatus.process_alive) {
        throw "$Operation is blocked by a live heartbeat without exact disabled-task ownership."
    }

    $PostHeartbeat = Get-GrandeAlphaHeartbeatStatus
    if (-not (Test-GrandeAlphaHeartbeatPidEvidenceSafe $PostHeartbeat)) {
        throw "$Operation is blocked because disabled-task cleanup left a malformed heartbeat PID."
    }
    $PostRuntime = Resolve-GrandeAlphaRuntime $PostHeartbeat
    if (
        $PostRuntime.state -notin @('NONE', 'STOPPED') -or
        $PostHeartbeat.process_alive
    ) {
        throw (
            "$Operation cannot mutate the Disabled task until lifecycle ownership is terminal " +
            'and no heartbeat PID is alive.'
        )
    }
    Write-Host (
        "$Operation accepted the exact-identity Disabled task only after proving its runtime is off."
    ) -ForegroundColor Yellow
}

function Stop-GrandeAlphaLifecycle {
    param(
        [Parameter(Mandatory = $true)]$Task,
        [Parameter(Mandatory = $true)]$Runtime
    )

    if ($Runtime.state -in @('INVALID_RECORD', 'UNVERIFIED')) {
        throw "Runtime ownership is $($Runtime.state); no task or process was changed. $($Runtime.detail)"
    }
    if ([string]$Task.State -notin @('Ready', 'Running', 'Queued')) {
        throw "Task Scheduler state '$($Task.State)' is not safe for a scoped stop."
    }
    $LaunchSnapshot = if (
        $Runtime.state -in @('OWNED', 'ORPHANED', 'LEGACY_OWNED') -and
        $Runtime.child_identity_valid
    ) {
        $Runtime.child
    } else {
        $null
    }
    $HeartbeatStatus = Get-GrandeAlphaHeartbeatStatus
    if (-not (Test-GrandeAlphaHeartbeatPidEvidenceSafe $HeartbeatStatus)) {
        throw 'Heartbeat process ID is malformed; no task or process was changed.'
    }
    if (
        $Runtime.state -in @('NONE', 'STOPPED') -and
        $HeartbeatStatus.process_alive
    ) {
        throw 'A heartbeat PID is alive without exact lifecycle ownership; no task or process was changed.'
    }
    $ApplicationRuntime = Get-GrandeAlphaApplicationRuntime `
        -Runtime $Runtime `
        -HeartbeatStatus $HeartbeatStatus
    if ($null -ne $LaunchSnapshot -and $ApplicationRuntime.state -eq 'UNVERIFIED') {
        throw (
            'The owned Python launcher is alive, but its application process is ambiguous rather than exactly ' +
            "attributable; no task or process was changed. $($ApplicationRuntime.detail)"
        )
    }
    if (-not (Test-GrandeAlphaHeartbeatPidAttributed $HeartbeatStatus $ApplicationRuntime)) {
        throw 'Heartbeat PID is alive but does not match the exact owned application; nothing was changed.'
    }
    $ChildSnapshot = if ($ApplicationRuntime.state -eq 'VERIFIED') {
        $ApplicationRuntime.process
    } else {
        $null
    }

    $CloseRequested = $false
    $NormalCloseCompleted = $false
    if ($null -ne $ChildSnapshot -and (Test-GrandeAlphaSnapshotStillExact $ChildSnapshot)) {
        try {
            $CloseRequested = $ChildSnapshot.native_process.CloseMainWindow()
        } catch {
            $CloseRequested = $false
        }
    }
    if ($CloseRequested) {
        $NormalCloseCompleted = Wait-GrandeAlphaProcessExit `
            -Snapshot $ChildSnapshot `
            -TimeoutSeconds $Spec.normal_close_timeout_seconds
    }

    $CurrentTask = Get-GrandeAlphaScheduledTask
    if ($null -eq $CurrentTask) {
        throw 'The installed task disappeared before the scoped stop request.'
    }
    if ([string]$CurrentTask.State -in @('Running', 'Queued')) {
        Stop-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath
    } elseif ([string]$CurrentTask.State -ne 'Ready') {
        throw "Task Scheduler entered unsupported state '$($CurrentTask.State)' before stop."
    }

    if ($null -ne $ChildSnapshot) {
        $RemainingStopSeconds = if ($CloseRequested) {
            $Spec.stop_timeout_seconds - $Spec.normal_close_timeout_seconds
        } else {
            $Spec.stop_timeout_seconds
        }
        $Exited = $NormalCloseCompleted -or (Wait-GrandeAlphaProcessExit `
            -Snapshot $ChildSnapshot `
            -TimeoutSeconds $RemainingStopSeconds
        )
        if (-not $Exited) {
            if (-not (Test-GrandeAlphaSnapshotStillExact $ChildSnapshot)) {
                throw 'The recorded child identity changed during stop; no termination was attempted.'
            }
            $ExactChild = Get-GrandeAlphaProcessSnapshot ([int]$ChildSnapshot.process_id)
            $ExactChild.native_process.Kill()
            if (-not (Wait-GrandeAlphaProcessExit -Snapshot $ChildSnapshot -TimeoutSeconds 5)) {
                throw "Exact owned child PID $($ChildSnapshot.process_id) did not exit after termination."
            }
            Write-Host (
                "Stopped exact owned child PID $($ChildSnapshot.process_id) after the " +
                'normal window-close request did not complete in time.'
            ) -ForegroundColor Yellow
        } elseif ($CloseRequested) {
            Write-Host "Exact owned child PID $($ChildSnapshot.process_id) accepted a normal window-close request and exited."
        } else {
            Write-Host "Exact owned child PID $($ChildSnapshot.process_id) exited while its task was being stopped."
        }
    }

    if (
        $null -ne $LaunchSnapshot -and
        [int]$LaunchSnapshot.process_id -ne [int]$ChildSnapshot.process_id -and
        -not (Wait-GrandeAlphaLaunchProcessExit -Snapshot $LaunchSnapshot -TimeoutSeconds 5)
    ) {
        if (-not (Test-GrandeAlphaLaunchSnapshotStillExact $LaunchSnapshot)) {
            throw 'The recorded Python launcher identity changed during stop; no launcher termination was attempted.'
        }
        $ExactLauncher = Get-GrandeAlphaProcessSnapshot ([int]$LaunchSnapshot.process_id)
        $ExactLauncher.native_process.Kill()
        if (-not (Wait-GrandeAlphaLaunchProcessExit -Snapshot $LaunchSnapshot -TimeoutSeconds 5)) {
            throw "Exact owned Python launcher PID $($LaunchSnapshot.process_id) did not exit after termination."
        }
    }

    $TaskStopDeadline = [datetimeoffset]::UtcNow.AddSeconds(5)
    do {
        $CurrentTask = Get-GrandeAlphaScheduledTask
        if ($null -ne $CurrentTask -and [string]$CurrentTask.State -eq 'Ready') {
            break
        }
        Start-Sleep -Milliseconds 250
    } while ([datetimeoffset]::UtcNow -lt $TaskStopDeadline)
    if ($null -eq $CurrentTask -or [string]$CurrentTask.State -ne 'Ready') {
        $ObservedTaskState = if ($null -eq $CurrentTask) { 'ABSENT' } else { [string]$CurrentTask.State }
        throw "Task Scheduler did not reach Ready after the scoped stop request (state $ObservedTaskState)."
    }

    $ReconcileDeadline = [datetimeoffset]::UtcNow.AddSeconds($Spec.post_stop_reconcile_seconds)
    $StableOffObservations = 0
    while ([datetimeoffset]::UtcNow -lt $ReconcileDeadline) {
        $PostStopTask = Get-GrandeAlphaScheduledTask
        if ($null -eq $PostStopTask -or [string]$PostStopTask.State -ne 'Ready') {
            $PostStopTaskState = if ($null -eq $PostStopTask) { 'ABSENT' } else { [string]$PostStopTask.State }
            throw "Task Scheduler left Ready during post-stop reconciliation (state $PostStopTaskState)."
        }
        $PostStopHeartbeat = Get-GrandeAlphaHeartbeatStatus
        if (-not (Test-GrandeAlphaHeartbeatPidEvidenceSafe $PostStopHeartbeat)) {
            throw 'Post-stop heartbeat has a malformed process ID; refusing to report success.'
        }
        $PostStopRuntime = Get-GrandeAlphaInstalledRuntime
        if ($PostStopRuntime.state -in @('OWNED', 'ORPHANED')) {
            $StableOffObservations = 0
            $LateApplication = Get-GrandeAlphaApplicationRuntime `
                -Runtime $PostStopRuntime `
                -HeartbeatStatus $PostStopHeartbeat
            if ($LateApplication.state -in @('VERIFIED', 'NONE')) {
                Stop-GrandeAlphaLateOwnedRuntime `
                    -Runtime $PostStopRuntime `
                    -HeartbeatStatus $PostStopHeartbeat
                continue
            }
            throw "Post-stop late application identity is $($LateApplication.state)."
        } elseif ($PostStopRuntime.state -in @('INVALID_RECORD', 'UNVERIFIED')) {
            throw "Post-stop lifecycle identity is $($PostStopRuntime.state); refusing to report success."
        } elseif (
            $PostStopRuntime.state -in @('STOPPED', 'NONE') -and
            -not $PostStopHeartbeat.process_alive
        ) {
            $StableOffObservations++
            if ($StableOffObservations -ge 2) {
                break
            }
        } else {
            $StableOffObservations = 0
        }
        Start-Sleep -Milliseconds 250
    }
    if ($StableOffObservations -lt 2) {
        throw (
            'Post-stop reconciliation did not prove two stable observations with Task Scheduler ' +
            'off, no owned child, and no live heartbeat process.'
        )
    }
    Write-Host 'GRANDE Alpha scheduled live shadow is stopped; the task remains installed.' -ForegroundColor Green
    Write-Host 'Durable shadow checkpoints and audit data were not deleted or reset.'
}

function Start-GrandeAlphaLifecycle {
    param([Parameter(Mandatory = $true)]$Task)

    if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
        throw "GRANDE Alpha's managed Python runtime is missing. Run setup.ps1 before starting the schedule."
    }
    if (-not (Test-Path -LiteralPath $PythonProcessExe -PathType Leaf)) {
        throw "GRANDE Alpha's Python process executable could not be resolved safely."
    }
    $BeforeHeartbeat = Get-GrandeAlphaHeartbeatStatus
    if (-not (Test-GrandeAlphaHeartbeatPidEvidenceSafe $BeforeHeartbeat)) {
        throw 'Heartbeat process ID is malformed; no task start was attempted.'
    }
    $BeforeRuntime = Resolve-GrandeAlphaRuntime $BeforeHeartbeat
    if ($BeforeRuntime.state -in @('INVALID_RECORD', 'UNVERIFIED')) {
        throw "Runtime ownership is $($BeforeRuntime.state); no task or process was changed. $($BeforeRuntime.detail)"
    }
    if (
        $BeforeRuntime.state -in @('NONE', 'STOPPED') -and
        $BeforeHeartbeat.process_alive
    ) {
        throw 'A heartbeat PID is alive without exact lifecycle ownership; no task start was attempted.'
    }
    if (
        [string]$Task.State -eq 'Running' -and
        $BeforeRuntime.state -in @('OWNED', 'LEGACY_OWNED', 'STARTING', 'RETRYING')
    ) {
        Write-Host "GRANDE Alpha scheduled live shadow is already running ($($BeforeRuntime.state))." -ForegroundColor Green
        return
    }
    if ($BeforeRuntime.state -eq 'ORPHANED') {
        Write-Host 'A verified orphaned scheduled child will be stopped before a new task instance starts.' -ForegroundColor Yellow
        Stop-GrandeAlphaLifecycle -Task $Task -Runtime $BeforeRuntime
        $Task = Assert-GrandeAlphaTaskContract
    } elseif (
        [string]$Task.State -ne 'Running' -and
        $BeforeHeartbeat.process_alive
    ) {
        throw 'A live heartbeat is not safely attributable to this task; no start was attempted.'
    }

    if ([string]$Task.State -notin @('Ready', 'Running', 'Queued')) {
        throw "Task Scheduler state '$($Task.State)' is not safe for a scoped start."
    }
    $StartedAfter = [datetimeoffset]::UtcNow
    if ([string]$Task.State -ne 'Queued') {
        Start-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath
    }
    $Deadline = $StartedAfter.AddSeconds($Spec.start_timeout_seconds)
    while ([datetimeoffset]::UtcNow -lt $Deadline) {
        Start-Sleep -Milliseconds 500
        $CurrentTask = Get-GrandeAlphaScheduledTask
        $Heartbeat = Get-GrandeAlphaHeartbeatStatus
        $Runtime = Resolve-GrandeAlphaRuntime $Heartbeat
        $ApplicationRuntime = Get-GrandeAlphaApplicationRuntime `
            -Runtime $Runtime `
            -HeartbeatStatus $Heartbeat
        if (
            [string]$CurrentTask.State -eq 'Running' -and
            $Runtime.state -eq 'OWNED' -and
            $Runtime.child_identity_valid -and
            $ApplicationRuntime.state -eq 'VERIFIED' -and
            $Heartbeat.liveness -eq 'EVENT_LOOP_FRESH' -and
            [datetimeoffset]::Parse([string]$Heartbeat.observed_at_utc) -ge $StartedAfter.AddSeconds(-2)
        ) {
            Write-Host (
                "Started exact current-user scheduled shadow instance $($Runtime.record.instance_id); " +
                "application PID $($ApplicationRuntime.process.process_id)."
            ) -ForegroundColor Green
            return
        }
        if ($Runtime.state -in @('INVALID_RECORD', 'UNVERIFIED', 'ORPHANED')) {
            throw "Start failed closed because runtime ownership became $($Runtime.state): $($Runtime.detail)"
        }
    }
    throw "Task start was requested, but a new owned child and fresh matching heartbeat were not proven within $($Spec.start_timeout_seconds) seconds."
}

function Write-ScheduleStatus {
    $Task = Get-GrandeAlphaScheduledTask
    if ($null -eq $Task) {
        Write-Host 'GRANDE Alpha scheduled live shadow: NOT INSTALLED' -ForegroundColor Yellow
        Write-Host 'No task was changed. Run with -Install to opt in.'
        return $false
    }

    $Mismatches = @(Get-GrandeAlphaTaskContractMismatches $Task)
    if ($Mismatches.Count -gt 0) {
        Write-Host 'GRANDE Alpha scheduled live shadow: INVALID / UNSAFE' -ForegroundColor Red
        foreach ($Mismatch in $Mismatches) {
            Write-Host "- $Mismatch" -ForegroundColor Red
        }
        Write-Host 'No task was changed. Reinstall only after reviewing the mismatches.'
        return $false
    }

    $Trigger = @($Task.Triggers)[0]
    $Action = @($Task.Actions)[0]
    $InstalledLocalTime = if ($Trigger.StartBoundary) {
        ([datetime]$Trigger.StartBoundary).ToString('HH:mm')
    } else {
        'unknown'
    }
    Write-Host 'GRANDE Alpha scheduled live shadow: INSTALLED / CONTRACT VALID' -ForegroundColor Green
    Write-Host "Task:             $TaskPath$TaskName"
    Write-Host "State:            $($Task.State)"
    Write-Host "User:             $($Task.Principal.UserId)"
    Write-Host "Logon / privilege: $($Task.Principal.LogonType) / $($Task.Principal.RunLevel)"
    Write-Host "Installed schedule: $InstalledLocalTime local time, Monday-Friday"
    Write-Host "Zone-derived target: $LocalTriggerTime local time (07:00-09:20 ET window)"
    Write-Host "Host time zone:    $HostTimeZoneId"
    Write-Host "Action:           $($Action.Execute) $($Action.Arguments)"
    Write-Host "Working directory: $($Action.WorkingDirectory)"
    Write-Host "Next run:          $((Get-ScheduledTaskInfo -TaskName $TaskName -TaskPath $TaskPath).NextRunTime)"
    Write-Host "Start when missed: $($Task.Settings.StartWhenAvailable)"
    Write-Host "Wake to run:       $($Task.Settings.WakeToRun)"
    Write-Host "Start on battery:  $(-not $Task.Settings.DisallowStartIfOnBatteries)"
    Write-Host "Stop on battery:   $($Task.Settings.StopIfGoingOnBatteries)"
    Write-Host "Network required:  $($Task.Settings.RunOnlyIfNetworkAvailable)"
    Write-Host "Overlap policy:    $($Task.Settings.MultipleInstances)"
    Write-Host "Task restart:      $($Task.Settings.RestartCount) attempts every $($Task.Settings.RestartInterval)"
    Write-Host "Trigger enabled:   $($Trigger.Enabled)"
    Write-Host 'Mode:              --auto-shadow (no live-order authorization)'
    $HeartbeatStatus = Get-GrandeAlphaHeartbeatStatus
    $Runtime = Resolve-GrandeAlphaRuntime $HeartbeatStatus
    $ApplicationRuntime = Get-GrandeAlphaApplicationRuntime `
        -Runtime $Runtime `
        -HeartbeatStatus $HeartbeatStatus
    $RuntimeColor = switch ($Runtime.state) {
        'OWNED' { 'Green' }
        'LEGACY_OWNED' { 'Yellow' }
        'STOPPED' { 'DarkGray' }
        'NONE' { 'DarkGray' }
        'STARTING' { 'Yellow' }
        'RETRYING' { 'Yellow' }
        default { 'Red' }
    }
    Write-Host "Runtime ownership: $($Runtime.state)" -ForegroundColor $RuntimeColor
    Write-Host "Ownership detail:  $($Runtime.detail)"
    Write-Host "Application identity: $($ApplicationRuntime.state)"
    Write-Host "Application detail:   $($ApplicationRuntime.detail)"
    $HeartbeatColor = switch ($HeartbeatStatus.operational_state) {
        'ACTIVE' { 'Green' }
        'WAITING' { 'Yellow' }
        default { 'Red' }
    }
    Write-Host (
        "Event-loop liveness: $($HeartbeatStatus.liveness) | state $($HeartbeatStatus.state) | " +
        "age $($HeartbeatStatus.age_seconds)s | PID $($HeartbeatStatus.process_id)"
    ) -ForegroundColor $HeartbeatColor
    Write-Host "Operational state:  $($HeartbeatStatus.operational_state)" -ForegroundColor $HeartbeatColor
    Write-Host (
        "Shadow runtime:    connected=$($HeartbeatStatus.connected) | " +
        "shadow_running=$($HeartbeatStatus.shadow_running) | " +
        "session_open=$($HeartbeatStatus.session_open) | " +
        "equity=$($HeartbeatStatus.shadow_equity) | P/L=$($HeartbeatStatus.shadow_pnl) | " +
        "fills=$($HeartbeatStatus.shadow_fills)"
    )
    Write-Host (
        "Data clocks:       refresh age $($HeartbeatStatus.last_refresh_age_seconds)s | " +
        "reconcile age $($HeartbeatStatus.last_reconcile_age_seconds)s"
    )
    Write-Host "Heartbeat detail:  $($HeartbeatStatus.detail)"
    Write-Host "Heartbeat file:    $HeartbeatPath"
    Write-Host "Lifecycle file:    $LifecyclePath"

    if (-not (Test-GrandeAlphaHeartbeatPidEvidenceSafe $HeartbeatStatus)) {
        Write-Host (
            'The heartbeat exists but its process ID is malformed; runtime ownership cannot be ' +
            'declared safely.'
        ) -ForegroundColor Red
        return $false
    }

    if ([string]$Task.State -eq 'Ready') {
        if ($Runtime.state -in @('OWNED', 'ORPHANED', 'LEGACY_OWNED', 'STARTING', 'RETRYING')) {
            Write-Host (
                "The task is Ready, but runtime ownership is $($Runtime.state); " +
                'the scheduler state does not account for the live wrapper or child.'
            ) -ForegroundColor Red
            return $false
        }
        if ($Runtime.state -in @('INVALID_RECORD', 'UNVERIFIED')) {
            Write-Host 'The task is Ready, but the lifecycle record or process identity is unsafe.' -ForegroundColor Red
            return $false
        }
        if ($HeartbeatStatus.process_alive) {
            Write-Host (
                'The task is Ready, but a heartbeat points to a live process without ' +
                'verified task ownership.'
            ) -ForegroundColor Red
            return $false
        }
        Write-Host 'Task runtime:       OFF (Ready with no verified live wrapper or child).' -ForegroundColor Green
        return $true
    }

    if ([string]$Task.State -eq 'Running') {
        if ($Runtime.state -notin @('OWNED', 'LEGACY_OWNED')) {
            Write-Host (
                "The task is Running, but runtime ownership is $($Runtime.state); " +
                'a healthy child is not yet proven.'
            ) -ForegroundColor Red
            return $false
        }
        if (
            $ApplicationRuntime.state -ne 'VERIFIED' -or
            $HeartbeatStatus.liveness -ne 'EVENT_LOOP_FRESH' -or
            ($HeartbeatStatus.session_open -and $HeartbeatStatus.operational_state -eq 'DEGRADED')
        ) {
            Write-Host 'The task and process identities match, but the running supervisor is not healthy.' -ForegroundColor Red
            return $false
        }
        return $true
    }

    Write-Host (
        "Task Scheduler state is $($Task.State); only exact Ready/OFF or Running/healthy " +
        'states can pass status.'
    ) -ForegroundColor Red
    return $false
}

if ($MyInvocation.InvocationName -eq '.') {
    return
}

if ($Status) {
    if (Write-ScheduleStatus) {
        exit 0
    }
    exit 1
}

if ($Start) {
    $Task = Assert-GrandeAlphaTaskContract
    Start-GrandeAlphaLifecycle -Task $Task
    if (Write-ScheduleStatus) {
        exit 0
    }
    exit 1
}

if ($Stop) {
    $Task = Assert-GrandeAlphaTaskContract
    $HeartbeatStatus = Get-GrandeAlphaHeartbeatStatus
    $Runtime = Resolve-GrandeAlphaRuntime $HeartbeatStatus
    if (
        $Runtime.state -in @('NONE', 'STOPPED') -and
        $HeartbeatStatus.process_alive
    ) {
        throw 'A live heartbeat is not safely attributable to this task; no task or process was changed.'
    }
    Stop-GrandeAlphaLifecycle -Task $Task -Runtime $Runtime
    exit 0
}

if ($Restart) {
    $Task = Assert-GrandeAlphaTaskContract
    $HeartbeatStatus = Get-GrandeAlphaHeartbeatStatus
    $Runtime = Resolve-GrandeAlphaRuntime $HeartbeatStatus
    if (
        $Runtime.state -in @('NONE', 'STOPPED') -and
        $HeartbeatStatus.process_alive
    ) {
        throw 'A live heartbeat is not safely attributable to this task; no task or process was changed.'
    }
    Stop-GrandeAlphaLifecycle -Task $Task -Runtime $Runtime
    $Task = Assert-GrandeAlphaTaskContract
    Start-GrandeAlphaLifecycle -Task $Task
    if (Write-ScheduleStatus) {
        exit 0
    }
    exit 1
}

if ($Remove) {
    $Existing = Get-GrandeAlphaScheduledTask
    if ($null -eq $Existing) {
        $CleanedRuntime = Invoke-GrandeAlphaAbsentTaskPreflight '-Remove'
        if ($CleanedRuntime) {
            Write-Host (
                'The task was already absent; its exact verified orphaned runtime was stopped.'
            ) -ForegroundColor Green
            exit 0
        }
        Write-Host 'GRANDE Alpha scheduled live shadow and its verified runtime are already absent.' -ForegroundColor Green
        exit 0
    }
    $IdentityMismatches = @(Get-GrandeAlphaTaskIdentityMismatches $Existing)
    if ($IdentityMismatches.Count -gt 0) {
        throw "Task ownership is not exact; no task was removed: $($IdentityMismatches -join '; ')"
    }
    $ContractMismatches = @(Get-GrandeAlphaTaskContractMismatches $Existing)
    if ($ContractMismatches.Count -gt 0) {
        Write-Host (
            'The task schedule/settings contract is invalid, but its current-user action identity ' +
            'is exact; the explicit remove will continue through scoped lifecycle shutdown.'
        ) -ForegroundColor Yellow
    }
    $HeartbeatStatus = Get-GrandeAlphaHeartbeatStatus
    $Runtime = Resolve-GrandeAlphaRuntime $HeartbeatStatus
    Stop-GrandeAlphaLifecycleForTaskMutation `
        -Task $Existing `
        -Runtime $Runtime `
        -HeartbeatStatus $HeartbeatStatus `
        -Operation '-Remove'
    Unregister-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -Confirm:$false
    Write-Host 'Removed the per-user GRANDE Alpha scheduled live-shadow task.' -ForegroundColor Green
    exit 0
}

if (-not [Environment]::UserInteractive) {
    throw "Install the schedule from the current user's interactive Windows session."
}
if ($null -eq $LocalTriggerTime) {
    throw "The scheduled shadow supports only the continental U.S. Eastern, Central, Mountain, Arizona, and Pacific Windows time zones. This computer reports '$HostTimeZoneId'; no task was changed."
}
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "GRANDE Alpha's managed Python runtime is missing. Run setup.ps1 before installing the schedule."
}
if (-not (Test-Path -LiteralPath $PythonProcessExe -PathType Leaf)) {
    throw "GRANDE Alpha's Python process executable could not be resolved safely."
}

$StartAfterInstall = $false
$Existing = Get-GrandeAlphaScheduledTask
if ($null -eq $Existing) {
    $StartAfterInstall = Invoke-GrandeAlphaAbsentTaskPreflight '-Install'
} else {
    $IdentityMismatches = @(Get-GrandeAlphaTaskIdentityMismatches $Existing)
    if ($IdentityMismatches.Count -gt 0) {
        throw "Task ownership is not exact; no task was replaced: $($IdentityMismatches -join '; ')"
    }
    $ContractMismatches = @(Get-GrandeAlphaTaskContractMismatches $Existing)
    if ($ContractMismatches.Count -gt 0) {
        Write-Host (
            'The task schedule/settings contract is invalid, but its current-user action identity ' +
            'is exact; the explicit install will replace it after scoped lifecycle shutdown.'
        ) -ForegroundColor Yellow
    }
    $HeartbeatStatus = Get-GrandeAlphaHeartbeatStatus
    $Runtime = Resolve-GrandeAlphaRuntime $HeartbeatStatus
    $StartAfterInstall = (
        [string]$Existing.State -in @('Running', 'Queued') -or
        $Runtime.state -in @('OWNED', 'ORPHANED', 'LEGACY_OWNED', 'STARTING', 'RETRYING')
    )
    Stop-GrandeAlphaLifecycleForTaskMutation `
        -Task $Existing `
        -Runtime $Runtime `
        -HeartbeatStatus $HeartbeatStatus `
        -Operation '-Install'
}

$ActionParameters = @{
    Execute = $PowerShellExe
    Argument = $ActionArguments
    WorkingDirectory = $ProjectRoot
}
$Action = New-ScheduledTaskAction @ActionParameters
$TriggerParameters = @{
    Weekly = $true
    WeeksInterval = 1
    DaysOfWeek = @('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')
    At = $LocalTriggerTime
}
$Trigger = New-ScheduledTaskTrigger @TriggerParameters
$PrincipalParameters = @{
    UserId = $CurrentUser
    LogonType = 'Interactive'
    RunLevel = 'Limited'
}
$Principal = New-ScheduledTaskPrincipal @PrincipalParameters
$SettingsParameters = @{
    MultipleInstances = 'IgnoreNew'
    StartWhenAvailable = $false
    WakeToRun = $true
    AllowStartIfOnBatteries = $true
    DontStopIfGoingOnBatteries = $true
    RunOnlyIfNetworkAvailable = $false
    RestartCount = $Spec.task_restart_count
    RestartInterval = [TimeSpan]::FromMinutes($Spec.task_restart_interval_minutes)
    # Across the explicitly supported continental-U.S. time zones, the mapped
    # local trigger falls between 07:00 and 09:20 ET. Fourteen hours extends
    # beyond the 4:00 ET close. Unsupported zones are rejected above instead of
    # silently creating a late launch or truncating the session-close receipt.
    ExecutionTimeLimit = [TimeSpan]::Zero
}
$Settings = New-ScheduledTaskSettingsSet @SettingsParameters
$TaskParameters = @{
    Action = $Action
    Trigger = $Trigger
    Principal = $Principal
    Settings = $Settings
    Description = "Launch GRANDE Alpha in readiness-gated live-shadow mode at $LocalTriggerTime local time on weekdays ($HostTimeZoneId)."
}
$Task = New-ScheduledTask @TaskParameters
$RegistrationParameters = @{
    TaskName = $TaskName
    TaskPath = $TaskPath
    InputObject = $Task
    Force = $true
}
Register-ScheduledTask @RegistrationParameters | Out-Null

Write-Host 'Installed or refreshed the per-user GRANDE Alpha live-shadow schedule.' -ForegroundColor Green
Write-Host 'It runs only in the current interactive user session, stores no password, and never catches up a missed start.'
if (-not (Write-ScheduleStatus)) {
    throw 'The installed task failed its post-registration contract validation.'
}
if ($StartAfterInstall) {
    $Task = Assert-GrandeAlphaTaskContract
    Start-GrandeAlphaLifecycle -Task $Task
    if (-not (Write-ScheduleStatus)) {
        throw 'The refreshed task did not return to a proven owned runtime.'
    }
}
