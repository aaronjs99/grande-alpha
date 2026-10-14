param([switch]$Packaged)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
. (Join-Path $PSScriptRoot 'runtime.ps1')
$Icon = Join-Path $ProjectRoot 'assets\brand\grande-alpha.ico'

if ($Packaged) {
    $ShortcutTarget = Join-Path $ProjectRoot 'dist\GRANDEAlpha\GRANDEAlpha.exe'
    if (-not (Test-Path -LiteralPath $ShortcutTarget -PathType Leaf)) {
        throw 'Build the Windows executable before installing packaged shortcuts.'
    }
    $ShortcutArguments = ''
} else {
    & (Join-Path $ProjectRoot 'grande.ps1') setup
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $ShortcutTarget = Get-GrandeAlphaPython $ProjectRoot -Windowed
    $ShortcutArguments = '-m grande_alpha.app'
}
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
    $Shortcut.TargetPath = $ShortcutTarget
    $Shortcut.Arguments = $ShortcutArguments
    $Shortcut.WorkingDirectory = $ProjectRoot
    $Shortcut.IconLocation = "$Icon,0"
    $Shortcut.Description = 'GRANDE Alpha research and consent-gated trading workstation'
    $Shortcut.Save()
    & $PythonExe -m grande_alpha.desktop.windows_shortcut $ShortcutPath
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
        $ObsoleteItem = Get-Item -LiteralPath $ObsoleteShortcut
        $ObsoleteLink = $Shell.CreateShortcut($ObsoleteShortcut)
        if (($ObsoleteItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
            $ObsoleteLink.WorkingDirectory -ne $ProjectRoot -or
            $ObsoleteLink.Arguments.IndexOf($ProjectRoot, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
            Write-Host "Kept custom shortcut: $ObsoleteShortcut"
            continue
        }
        $RuntimeDirectory = & $PythonExe -c 'from grande_alpha.configuration.config import data_dir; print(data_dir())'
        if ($LASTEXITCODE -ne 0) { throw 'Could not locate the retired-shortcut backup directory.' }
        $BackupDirectory = Join-Path ([IO.Path]::GetFullPath($RuntimeDirectory.Trim())) 'retired-shortcuts'
        New-Item -ItemType Directory -Path $BackupDirectory -Force | Out-Null
        $ShortcutHash = (Get-FileHash -LiteralPath $ObsoleteShortcut -Algorithm SHA256).Hash
        $BackupPath = Join-Path $BackupDirectory "$ShortcutHash-$($ObsoleteItem.Name)"
        if (-not (Test-Path -LiteralPath $BackupPath)) {
            Copy-Item -LiteralPath $ObsoleteShortcut -Destination $BackupPath
        }
        if ((Get-FileHash -LiteralPath $BackupPath -Algorithm SHA256).Hash -ne $ShortcutHash -or
            (Get-FileHash -LiteralPath $ObsoleteShortcut -Algorithm SHA256).Hash -ne $ShortcutHash) {
            throw "Retired shortcut changed or its backup could not be verified: $ObsoleteShortcut"
        }
        Remove-Item -LiteralPath $ObsoleteShortcut -Force
        Write-Host "Retired shortcut preserved at: $BackupPath" -ForegroundColor Yellow
    }
}

if (-not $Packaged) {
    & (Join-Path $ProjectRoot 'grande.ps1') doctor
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host 'Local installation complete. Start GRANDE Alpha from its desktop or Start Menu shortcut.' -ForegroundColor Green
