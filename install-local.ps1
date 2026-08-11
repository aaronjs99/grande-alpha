$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ProjectRoot 'runtime-path.ps1')
$PowerShell = (Get-Command powershell.exe -ErrorAction Stop).Source
$CommandPrompt = (Get-Command cmd.exe -ErrorAction Stop).Source
$Icon = Join-Path $ProjectRoot 'assets\brand\grande-alpha.ico'
$Launcher = Join-Path $ProjectRoot 'run.ps1'
$MorningCheck = Join-Path $ProjectRoot 'Morning Check.cmd'
$ScheduleSetup = Join-Path $ProjectRoot 'Scheduled Shadow Setup.cmd'

& (Join-Path $ProjectRoot 'setup.ps1')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$PythonExe = Get-GrandeAlphaPython $ProjectRoot

$Desktop = [Environment]::GetFolderPath('Desktop')
$StartMenu = Join-Path ([Environment]::GetFolderPath('Programs')) 'GRANDE Alpha'
New-Item -ItemType Directory -Path $StartMenu -Force | Out-Null
$Shell = New-Object -ComObject WScript.Shell

foreach ($ShortcutPath in @(
    (Join-Path $Desktop 'GRANDE Alpha.lnk'),
    (Join-Path $StartMenu 'GRANDE Alpha.lnk')
)) {
    $Shortcut = $Shell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = $PowerShell
    $Shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$Launcher`""
    $Shortcut.WorkingDirectory = $ProjectRoot
    $Shortcut.IconLocation = "$Icon,0"
    $Shortcut.Description = 'GRANDE Alpha research and consent-gated trading workstation'
    $Shortcut.Save()
    & $PythonExe -m grande_alpha.windows_shortcut $ShortcutPath
    if ($LASTEXITCODE -ne 0) {
        throw "Could not assign the GRANDE Alpha taskbar identity to $ShortcutPath"
    }
    Write-Host "Installed shortcut: $ShortcutPath" -ForegroundColor Green
}

$ScheduleShortcutPath = Join-Path $StartMenu 'GRANDE Alpha Shadow Schedule.lnk'
$Shortcut = $Shell.CreateShortcut($ScheduleShortcutPath)
$Shortcut.TargetPath = $CommandPrompt
$Shortcut.Arguments = "/c `"`"$ScheduleSetup`"`""
$Shortcut.WorkingDirectory = $ProjectRoot
$Shortcut.IconLocation = "$Icon,0"
$Shortcut.Description = 'Opt-in setup, status, and removal for the weekday live-shadow schedule'
$Shortcut.Save()
Write-Host "Installed shortcut: $ScheduleShortcutPath" -ForegroundColor Green

foreach ($ShortcutPath in @(
    (Join-Path $Desktop 'GRANDE Alpha Morning Check.lnk'),
    (Join-Path $StartMenu 'GRANDE Alpha Morning Check.lnk')
)) {
    $Shortcut = $Shell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = $CommandPrompt
    $Shortcut.Arguments = "/c `"`"$MorningCheck`"`""
    $Shortcut.WorkingDirectory = $ProjectRoot
    $Shortcut.IconLocation = "$Icon,0"
    $Shortcut.Description = 'Read-only GRANDE Alpha and Robinhood morning readiness check'
    $Shortcut.Save()
    Write-Host "Installed shortcut: $ShortcutPath" -ForegroundColor Green
}

& (Join-Path $ProjectRoot 'doctor.ps1')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host 'Local installation complete. Run GRANDE Alpha Morning Check, then start GRANDE Alpha.' -ForegroundColor Green
Write-Host 'Optional: the Start Menu Shadow Schedule shortcut can install, inspect, or remove the 6:20 AM weekday task. No task was enabled by this installer.' -ForegroundColor Yellow
