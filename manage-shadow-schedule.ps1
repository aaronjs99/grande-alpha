[CmdletBinding()]
param(
    [switch]$Install,
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

$SelectedModes = @($Install.IsPresent, $Status.IsPresent, $Remove.IsPresent, $Definition.IsPresent) |
    Where-Object { $_ }
if ($SelectedModes.Count -gt 1) {
    throw 'Choose only one mode: -Install, -Status, -Remove, or -Definition.'
}
if ($SelectedModes.Count -eq 0) {
    $Status = $true
}

$TaskName = 'GRANDE Alpha Live Shadow'
$TaskPath = '\'
$ProjectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $MyInvocation.MyCommand.Path))
$Launcher = [IO.Path]::GetFullPath((Join-Path $ProjectRoot 'scheduled-shadow.ps1'))
$PowerShellExe = [IO.Path]::GetFullPath((Get-Command powershell.exe -ErrorAction Stop).Source)
$CurrentUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$HostTimeZoneId = [TimeZoneInfo]::Local.Id
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

foreach ($Path in @($ProjectRoot, $Launcher, $PowerShellExe)) {
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
if (-not (Test-Path -LiteralPath $PowerShellExe -PathType Leaf)) {
    throw "Windows PowerShell not found: $PowerShellExe"
}

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
    application_mode = '--auto-shadow'
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

    return $Mismatches.ToArray()
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
    Write-Host "Trigger enabled:   $($Trigger.Enabled)"
    Write-Host 'Mode:              --auto-shadow (no live-order authorization)'
    return $true
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

if ($Remove) {
    $Existing = Get-GrandeAlphaScheduledTask
    if ($null -eq $Existing) {
        Write-Host 'GRANDE Alpha scheduled live shadow is already absent.' -ForegroundColor Green
        exit 0
    }
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
