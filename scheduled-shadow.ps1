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
$PythonExe = [IO.Path]::GetFullPath((Get-GrandeAlphaPython $ProjectRoot -Windowed))
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

try {
    Write-ScheduledShadowLog 'Launching GRANDE Alpha in --auto-shadow mode.'
    $LaunchParameters = @{
        FilePath = $PythonExe
        ArgumentList = @('-m', 'grande_alpha.app', '--auto-shadow')
        WorkingDirectory = $ProjectRoot
        PassThru = $true
        Wait = $true
    }
    $Process = Start-Process @LaunchParameters
    Write-ScheduledShadowLog "GRANDE Alpha exited with code $($Process.ExitCode)."
    exit $Process.ExitCode
} catch {
    Write-ScheduledShadowLog "Scheduled shadow launch failed: $($_.Exception.Message)"
    throw
}
