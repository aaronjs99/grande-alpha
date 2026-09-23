param([switch]$Full, [switch]$Broker)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
. (Join-Path $PSScriptRoot 'runtime.ps1')
$PythonExe = Get-GrandeAlphaPython $ProjectRoot
$Candidate = Join-Path $ProjectRoot 'dist\GRANDEAlpha\GRANDEAlpha.exe'
$SourceReady = $true

function Write-Check([string]$Name, [string]$Status, [ConsoleColor]$Color) {
    Write-Host ('{0,-28} {1}' -f $Name, $Status) -ForegroundColor $Color
}

Write-Host 'GRANDE Alpha readiness doctor' -ForegroundColor Cyan
Write-Host ('Repository: {0}' -f $ProjectRoot)

try {
    $Version = & $PythonExe -c "from grande_alpha import __version__; print(__version__)" 2>$null
    $ImportReady = $LASTEXITCODE -eq 0
} catch {
    $ImportReady = $false
}
if (-not $ImportReady) {
    Write-Check 'Source environment' 'MISSING - run .\grande.ps1 setup' Red
    $SourceReady = $false
} else {
    Write-Check 'Source environment' ('READY - version {0}' -f $Version) Green
    Write-Check 'Python location' $PythonExe DarkGray
    $PythonSignature = Get-AuthenticodeSignature -LiteralPath $PythonExe
    $PythonStatus = if ($PythonSignature.Status -eq 'Valid') { 'VALID trusted Python signature' } else { $PythonSignature.Status }
    Write-Check 'Python launcher' $PythonStatus $(if ($PythonSignature.Status -eq 'Valid') { 'Green' } else { 'Yellow' })

    $DesktopShortcut = Join-Path ([Environment]::GetFolderPath('Desktop')) 'GRANDE Alpha.lnk'
    $StartShortcut = Join-Path (Join-Path ([Environment]::GetFolderPath('Programs')) 'GRANDE Alpha') 'GRANDE Alpha.lnk'
    $ShortcutPaths = @($DesktopShortcut, $StartShortcut)
    $ExistingShortcuts = @($ShortcutPaths | Where-Object { Test-Path -LiteralPath $_ })
    if ($ExistingShortcuts.Count -eq $ShortcutPaths.Count) {
        & $PythonExe -m grande_alpha.windows_shortcut --check @ExistingShortcuts | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Check 'Taskbar identity' 'READY - GRANDE Alpha logo pin' Green
        } else {
            Write-Check 'Taskbar identity' 'MISMATCH - rerun .\grande.ps1 install' Red
            $SourceReady = $false
        }
    } else {
        Write-Check 'Taskbar identity' 'NOT INSTALLED - run .\grande.ps1 install' DarkGray
    }

    $BrokerState = & $PythonExe -c "import asyncio; from grande_alpha.broker.oauth import CredentialTokenStorage; s=CredentialTokenStorage(); print('STORED - run -Broker to verify' if asyncio.run(s.get_tokens()) is not None else 'NOT STORED')"
    if ($LASTEXITCODE -ne 0) { throw 'Broker credential readiness check failed' }
    Write-Check 'Robinhood OAuth' $BrokerState Yellow
}

if (Test-Path -LiteralPath $Candidate) {
    $CandidateSignature = Get-AuthenticodeSignature -LiteralPath $Candidate
    if ($CandidateSignature.Status -eq 'Valid') {
        Write-Check 'Packaged executable' 'SIGNED AND READY' Green
    } else {
        Write-Check 'Packaged executable' 'UNSIGNED CANDIDATE - may be blocked' Yellow
    }
} else {
    Write-Check 'Packaged executable' 'NOT BUILT - optional' DarkGray
}

if ($Full -and $SourceReady) {
    Write-Host ''
    Write-Host 'Running full verification...' -ForegroundColor Cyan
    & (Join-Path $ProjectRoot 'grande.ps1') verify
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if ($Broker -and $SourceReady) {
    Write-Host ''
    Write-Host 'Starting explicit read-only Robinhood OAuth and data check...' -ForegroundColor Cyan
    & $PythonExe -m grande_alpha.broker_check
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host ''
if ($SourceReady) {
    Write-Host 'SOURCE APP READY: run .\grande.ps1 run or Start GRANDE Alpha.cmd' -ForegroundColor Green
    exit 0
}
Write-Host 'NOT READY: follow the failing check above, then rerun this doctor.' -ForegroundColor Red
exit 1
