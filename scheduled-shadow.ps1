$ErrorActionPreference = 'Stop'

function Test-FullyQualifiedPath([string]$Path) {
    if (-not [IO.Path]::IsPathRooted($Path)) {
        return $false
    }
    $Expanded = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    return [string]::Equals($Expanded, $Path.TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase)
}

$ProjectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $MyInvocation.MyCommand.Path))
$Launcher = [IO.Path]::GetFullPath($MyInvocation.MyCommand.Path)
$RuntimeHelper = Join-Path $ProjectRoot 'runtime-path.ps1'
$LifecycleHelper = Join-Path $ProjectRoot 'shadow-lifecycle.ps1'
if (
    -not (Test-FullyQualifiedPath $ProjectRoot) -or
    -not (Test-Path -LiteralPath $RuntimeHelper -PathType Leaf) -or
    -not (Test-Path -LiteralPath $LifecycleHelper -PathType Leaf)
) {
    throw 'The scheduled-shadow launcher must run from a complete GRANDE Alpha source installation.'
}

. $RuntimeHelper
. $LifecycleHelper
$PythonExe = [IO.Path]::GetFullPath((Get-GrandeAlphaPython $ProjectRoot))
$PowerShellExe = [IO.Path]::GetFullPath((Get-Command powershell.exe -ErrorAction Stop).Source)
if (-not (Test-FullyQualifiedPath $PythonExe) -or -not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "GRANDE Alpha's managed Python runtime is missing. Run setup.ps1 before installing the schedule."
}
$PythonProcessExe = Get-GrandeAlphaPythonProcessExecutable $PythonExe
if (-not (Test-FullyQualifiedPath $PythonProcessExe) -or -not (Test-Path -LiteralPath $PythonProcessExe -PathType Leaf)) {
    throw "GRANDE Alpha's Python process executable could not be resolved safely."
}

$TaskName = 'GRANDE Alpha Live Shadow'
$CurrentUserSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$ActionArguments = "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Launcher`""
$LogDirectory = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'GRANDEAlpha'))
$LogPath = Join-Path $LogDirectory 'scheduled-shadow.log'
$LifecyclePath = Join-Path $LogDirectory 'scheduled-shadow-lifecycle.json'
New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null

function Write-ScheduledShadowLog([string]$Message) {
    $Timestamp = Get-Date -Format 'yyyy-MM-ddTHH:mm:ssK'
    Add-Content -LiteralPath $LogPath -Value "$Timestamp $Message" -Encoding utf8
}

function Set-ScheduledShadowLifecycle {
    param(
        [Parameter(Mandatory = $true)][string]$State,
        [Diagnostics.Process]$ChildProcess = $null,
        [switch]$ClearChild
    )

    $Lifecycle.state = $State
    $Lifecycle.observed_at_utc = [datetimeoffset]::UtcNow.ToString('o')
    if ($ClearChild) {
        $Lifecycle.child_process_id = $null
        $Lifecycle.child_started_at_utc = $null
    } elseif ($null -ne $ChildProcess) {
        $Lifecycle.child_process_id = [int]$ChildProcess.Id
        $Lifecycle.child_started_at_utc = $ChildProcess.StartTime.ToUniversalTime().ToString('o')
    }
    Write-GrandeAlphaAtomicJson -Path $LifecyclePath -Value $Lifecycle
}

$InitialRestartDelaySeconds = 15
$MaximumRestartDelaySeconds = 300
$StableRuntimeSeconds = 300
$RestartDelaySeconds = $InitialRestartDelaySeconds
$LaunchAttempt = 0
$Mutex = New-Object Threading.Mutex($false, 'Local\GRANDEAlpha.ScheduledShadow')
$MutexAcquired = $false

try {
    try {
        $MutexAcquired = $Mutex.WaitOne(0)
    } catch [Threading.AbandonedMutexException] {
        $MutexAcquired = $true
    }
    if (-not $MutexAcquired) {
        Write-ScheduledShadowLog 'Launch refused because another scheduled-shadow wrapper owns the current-user lifecycle mutex.'
        exit 4
    }

    $PriorRuntime = Get-GrandeAlphaOwnedRuntime `
        -LifecyclePath $LifecyclePath `
        -ProjectRoot $ProjectRoot `
        -Launcher $Launcher `
        -PythonLauncher $PythonExe `
        -PythonProcessExecutable $PythonProcessExe `
        -PowerShellExe $PowerShellExe `
        -ActionArguments $ActionArguments `
        -CurrentUserSid $CurrentUserSid `
        -TaskName $TaskName
    if ($PriorRuntime.state -in @('OWNED', 'ORPHANED', 'STARTING', 'RETRYING')) {
        Write-ScheduledShadowLog (
            "Launch refused because lifecycle state $($PriorRuntime.state) indicates an existing " +
            'owned wrapper or child. Use manage-shadow-schedule.ps1 -Restart to recover it safely.'
        )
        exit 5
    }
    if ($PriorRuntime.state -in @('INVALID_RECORD', 'UNVERIFIED')) {
        Write-ScheduledShadowLog (
            "Launch refused because lifecycle state $($PriorRuntime.state) cannot be attributed safely. " +
            'No process was changed.'
        )
        exit 6
    }

    $WrapperProcess = [Diagnostics.Process]::GetCurrentProcess()
    $Lifecycle = [ordered]@{
        schema_version = 1
        instance_id = [guid]::NewGuid().ToString('D')
        task_name = $TaskName
        mode = '--auto-shadow'
        read_only = $true
        broker_writes = $false
        live_authority = $false
        owner_sid = $CurrentUserSid
        project_root = $ProjectRoot
        launcher_path = $Launcher
        python_executable = $PythonExe
        python_process_executable = $PythonProcessExe
        wrapper_process_id = [int]$WrapperProcess.Id
        wrapper_started_at_utc = $WrapperProcess.StartTime.ToUniversalTime().ToString('o')
        child_process_id = $null
        child_started_at_utc = $null
        state = 'starting'
        observed_at_utc = [datetimeoffset]::UtcNow.ToString('o')
    }
    Set-ScheduledShadowLifecycle -State 'starting'

    while ($true) {
        $LaunchAttempt++
        $LaunchStartedAt = Get-Date
        Set-ScheduledShadowLifecycle -State 'launching' -ClearChild
        Write-ScheduledShadowLog "Launching GRANDE Alpha in --auto-shadow mode (attempt $LaunchAttempt)."

        $Child = $null
        $OwnedChild = $null
        try {
            $OwnedChild = Start-GrandeAlphaSuspendedJobProcess `
                -Executable $PythonExe `
                -Arguments '-m grande_alpha.app --auto-shadow' `
                -WorkingDirectory $ProjectRoot
            $Child = $OwnedChild.Process
            Set-ScheduledShadowLifecycle -State 'running' -ChildProcess $Child
            # The exact identity is durable before Python can execute. If the
            # wrapper exits on either side of this call, closing its anonymous
            # KILL_ON_JOB_CLOSE handle terminates the full launcher/app tree.
            $OwnedChild.Resume()
            $Child.WaitForExit()
            $ExitCode = [int]$Child.ExitCode
        } finally {
            if ($null -ne $OwnedChild) {
                $OwnedChild.Dispose()
            }
        }

        $RuntimeSeconds = [Math]::Max(0, [int]((Get-Date) - $LaunchStartedAt).TotalSeconds)
        if ($ExitCode -eq 0) {
            Set-ScheduledShadowLifecycle -State 'clean_exit'
            Write-ScheduledShadowLog (
                "GRANDE Alpha exited cleanly after ${RuntimeSeconds}s; " +
                'the read-only supervisor will remain off until the next task start.'
            )
            exit 0
        }
        if ($RuntimeSeconds -ge $StableRuntimeSeconds) {
            $RestartDelaySeconds = $InitialRestartDelaySeconds
        }
        Set-ScheduledShadowLifecycle -State 'restart_wait'
        Write-ScheduledShadowLog (
            "GRANDE Alpha exited with code $ExitCode after ${RuntimeSeconds}s; " +
            "read-only supervisor restart in ${RestartDelaySeconds}s."
        )
        Start-Sleep -Seconds $RestartDelaySeconds
        $RestartDelaySeconds = [Math]::Min(
            $MaximumRestartDelaySeconds,
            $RestartDelaySeconds * 2
        )
    }
} catch {
    if ($null -ne $Lifecycle) {
        try {
            Set-ScheduledShadowLifecycle -State 'failed'
        } catch {
            Write-ScheduledShadowLog "Lifecycle failure could not be recorded: $($_.Exception.Message)"
        }
    }
    Write-ScheduledShadowLog "Scheduled shadow launch failed: $($_.Exception.Message)"
    throw
} finally {
    if ($MutexAcquired) {
        $Mutex.ReleaseMutex()
    }
    $Mutex.Dispose()
}
