param([switch]$Full)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ProjectRoot 'runtime-path.ps1')
$RuntimeRoot = Get-GrandeAlphaRuntimeRoot $ProjectRoot
$VenvPython = Get-GrandeAlphaPython $ProjectRoot
$Candidate = Join-Path $ProjectRoot 'dist\GRANDEAlpha\GRANDEAlpha.exe'
$SourceReady = $true

function Write-Check([string]$Name, [string]$Status, [ConsoleColor]$Color) {
    Write-Host ('{0,-28} {1}' -f $Name, $Status) -ForegroundColor $Color
}

Write-Host 'GRANDE Alpha readiness doctor' -ForegroundColor Cyan
Write-Host ('Repository: {0}' -f $ProjectRoot)

if (-not (Test-Path -LiteralPath $VenvPython)) {
    Write-Check 'Source environment' 'MISSING - run .\setup.ps1' Red
    $SourceReady = $false
} else {
    $Version = & $VenvPython -c "from grande_alpha import __version__; print(__version__)"
    if ($LASTEXITCODE -ne 0) { throw 'GRANDE Alpha import failed' }
    Write-Check 'Source environment' ('READY - version {0}' -f $Version) Green
    Write-Check 'Runtime location' $RuntimeRoot DarkGray
    $PythonSignature = Get-AuthenticodeSignature -LiteralPath $VenvPython
    $PythonStatus = if ($PythonSignature.Status -eq 'Valid') { 'VALID trusted Python signature' } else { $PythonSignature.Status }
    Write-Check 'Python launcher' $PythonStatus $(if ($PythonSignature.Status -eq 'Valid') { 'Green' } else { 'Yellow' })

    $BrokerState = & $VenvPython -c "import asyncio; from grande_alpha.broker.oauth import CredentialTokenStorage; s=CredentialTokenStorage(); print('CONFIGURED' if asyncio.run(s.get_tokens()) is not None else 'NOT CONFIGURED')"
    if ($LASTEXITCODE -ne 0) { throw 'Broker credential readiness check failed' }
    Write-Check 'Robinhood OAuth' $BrokerState $(if ($BrokerState -eq 'CONFIGURED') { 'Green' } else { 'Yellow' })
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
    & (Join-Path $ProjectRoot 'verify.ps1')
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host ''
if ($SourceReady) {
    Write-Host 'SOURCE APP READY: run .\run.ps1 or Start GRANDE Alpha.cmd' -ForegroundColor Green
    exit 0
}
Write-Host 'NOT READY: run .\setup.ps1, then rerun this doctor.' -ForegroundColor Red
exit 1
