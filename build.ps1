$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

& $PythonExe -m PyInstaller --noconfirm --clean --windowed --name MomentumTrader `
    --collect-all keyring `
    --hidden-import mcp.client.auth.oauth2 --hidden-import mcp.client.streamable_http `
    --hidden-import mcp.shared.auth `
    --paths (Join-Path $ProjectRoot 'src') `
    (Join-Path $ProjectRoot 'src\momentum_trader\app.py')
$BuildExitCode = $LASTEXITCODE
if ($BuildExitCode -ne 0) {
    throw "PyInstaller failed with exit code $BuildExitCode"
}
$Executable = Join-Path $ProjectRoot 'dist\MomentumTrader\MomentumTrader.exe'
if (-not (Test-Path -LiteralPath $Executable)) {
    throw "PyInstaller completed without producing $Executable"
}
Write-Host "Built: $ProjectRoot\dist\MomentumTrader\MomentumTrader.exe" -ForegroundColor Green
