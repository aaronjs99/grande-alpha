$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
. (Join-Path $PSScriptRoot 'runtime.ps1')
$PowerShell = (Get-Command powershell.exe -ErrorAction Stop).Source
$Icon = Join-Path $ProjectRoot 'assets\brand\grande-alpha.ico'
$Launcher = Join-Path $ProjectRoot 'grande.ps1'

& (Join-Path $ProjectRoot 'grande.ps1') setup
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
    $Shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$Launcher`" run"
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

foreach ($ObsoleteShortcut in @(
    (Join-Path $Desktop 'GRANDE Alpha Morning Check.lnk'),
    (Join-Path $StartMenu 'GRANDE Alpha Morning Check.lnk'),
    (Join-Path $StartMenu 'GRANDE Alpha Shadow Schedule.lnk')
)) {
    if (Test-Path -LiteralPath $ObsoleteShortcut -PathType Leaf) {
        Remove-Item -LiteralPath $ObsoleteShortcut -Force
        Write-Host "Removed obsolete shortcut: $ObsoleteShortcut" -ForegroundColor Yellow
    }
}

& (Join-Path $ProjectRoot 'grande.ps1') doctor
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host 'Local installation complete. Start GRANDE Alpha from its desktop or Start Menu shortcut.' -ForegroundColor Green
