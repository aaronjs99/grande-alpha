$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ProjectRoot 'runtime-path.ps1')
$PythonExe = Get-GrandeAlphaPython $ProjectRoot

Write-Host 'GRANDE Alpha morning check' -ForegroundColor Cyan
Write-Host 'This is a read-only readiness check. It cannot submit, review, or cancel an order.'
Write-Host ''

& (Join-Path $ProjectRoot 'doctor.ps1') -Broker
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ''
Write-Host 'Local evidence and permission state' -ForegroundColor Cyan
& $PythonExe -m grande_alpha.cli status --width 120
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ''
Write-Host 'READY FOR RESEARCH AND LIVE SHADOW.' -ForegroundColor Green
Write-Host 'Real orders remain unavailable unless every evidence gate passes and you separately authorize a bounded live session.' -ForegroundColor Yellow
