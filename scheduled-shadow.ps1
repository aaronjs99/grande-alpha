$ErrorActionPreference = 'Stop'

function Test-FullyQualifiedPath([string]$Path) {
    if (-not [IO.Path]::IsPathRooted($Path)) {
        return $false
    }
    $Expanded = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    return [string]::Equals($Expanded, $Path.TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase)
}

$ProjectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $MyInvocation.MyCommand.Path))
$RuntimeHelper = Join-Path $ProjectRoot 'runtime-path.ps1'
if (-not (Test-FullyQualifiedPath $ProjectRoot) -or -not (Test-Path -LiteralPath $RuntimeHelper -PathType Leaf)) {
    throw 'The scheduled-shadow launcher must run from a complete GRANDE Alpha source installation.'
}

. $RuntimeHelper
$PythonExe = [IO.Path]::GetFullPath((Get-GrandeAlphaPython $ProjectRoot))
if (-not (Test-FullyQualifiedPath $PythonExe) -or -not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "GRANDE Alpha's managed Python runtime is missing. Run setup.ps1 before installing the schedule."
}

$LogDirectory = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'GRANDEAlpha'))
$LogPath = Join-Path $LogDirectory 'scheduled-shadow.log'
New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null

function Write-ScheduledShadowLog([string]$Message) {
    $Timestamp = Get-Date -Format 'yyyy-MM-ddTHH:mm:ssK'
    Add-Content -LiteralPath $LogPath -Value "$Timestamp $Message" -Encoding utf8
}

$InitialRestartDelaySeconds = 15
$MaximumRestartDelaySeconds = 300
$StableRuntimeSeconds = 300
$RestartDelaySeconds = $InitialRestartDelaySeconds
$LaunchAttempt = 0

try {
    while ($true) {
        $LaunchAttempt++
        $LaunchStartedAt = Get-Date
        Write-ScheduledShadowLog "Launching GRANDE Alpha in --auto-shadow mode (attempt $LaunchAttempt)."
        Push-Location -LiteralPath $ProjectRoot
        try {
            # Run as a foreground child of the hidden Task Scheduler PowerShell host.
            # Start-Process can outlive its wrapper after the wrapper is interrupted,
            # leaving Task Scheduler Ready while an unowned retry process survives.
            & $PythonExe -m grande_alpha.app --auto-shadow
            $ExitCode = if ($null -eq $LASTEXITCODE) { 1 } else { [int]$LASTEXITCODE }
        } finally {
            Pop-Location
        }

        $RuntimeSeconds = [Math]::Max(0, [int]((Get-Date) - $LaunchStartedAt).TotalSeconds)
        if ($ExitCode -eq 0) {
            Write-ScheduledShadowLog (
                "GRANDE Alpha exited cleanly after ${RuntimeSeconds}s; " +
                'the read-only supervisor will remain off until the next task start.'
            )
            exit 0
        }
        if ($RuntimeSeconds -ge $StableRuntimeSeconds) {
            $RestartDelaySeconds = $InitialRestartDelaySeconds
        }
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
    Write-ScheduledShadowLog "Scheduled shadow launch failed: $($_.Exception.Message)"
    throw
}
