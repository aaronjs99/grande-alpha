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
    local_time = '06:20'
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
    execution_time_limit_hours = 10
    application_mode = '--auto-shadow'
}

if ($Definition) {
    $Spec | ConvertTo-Json -Depth 4
    exit 0
}

function Get-GrandeAlphaScheduledTask {
    return Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -ErrorAction SilentlyContinue
}

function Write-ScheduleStatus {
    $Task = Get-GrandeAlphaScheduledTask
    if ($null -eq $Task) {
        Write-Host 'GRANDE Alpha scheduled live shadow: NOT INSTALLED' -ForegroundColor Yellow
        Write-Host 'No task was changed. Run with -Install to opt in.'
        return
    }

    $Trigger = @($Task.Triggers)[0]
    $Action = @($Task.Actions)[0]
    Write-Host 'GRANDE Alpha scheduled live shadow: INSTALLED' -ForegroundColor Green
    Write-Host "Task:             $TaskPath$TaskName"
    Write-Host "State:            $($Task.State)"
    Write-Host "User:             $($Task.Principal.UserId)"
    Write-Host "Logon / privilege: $($Task.Principal.LogonType) / $($Task.Principal.RunLevel)"
    Write-Host 'Schedule:         6:20 AM local time, Monday-Friday'
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
}

if ($Status) {
    Write-ScheduleStatus
    exit 0
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
    At = '06:20'
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
    ExecutionTimeLimit = (New-TimeSpan -Hours 10)
}
$Settings = New-ScheduledTaskSettingsSet @SettingsParameters
$TaskParameters = @{
    Action = $Action
    Trigger = $Trigger
    Principal = $Principal
    Settings = $Settings
    Description = 'Launch GRANDE Alpha in readiness-gated live-shadow mode at 6:20 AM local weekdays.'
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
Write-ScheduleStatus
