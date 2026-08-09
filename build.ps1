$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$IconIco = Join-Path $ProjectRoot 'assets\brand\grande-alpha.ico'
$IconPng = Join-Path $ProjectRoot 'src\grande_alpha\assets\app-icon.png'

& $PythonExe -m PyInstaller --noconfirm --clean --windowed --name GRANDEAlpha `
    --icon $IconIco --add-data "${IconPng};grande_alpha/assets" `
    --collect-all keyring `
    --collect-data rfc3987_syntax `
    --hidden-import mcp.client.auth.oauth2 --hidden-import mcp.client.streamable_http `
    --hidden-import mcp.shared.auth `
    --paths (Join-Path $ProjectRoot 'src') `
    (Join-Path $ProjectRoot 'src\grande_alpha\app.py')
$BuildExitCode = $LASTEXITCODE
if ($BuildExitCode -ne 0) {
    throw "PyInstaller failed with exit code $BuildExitCode"
}
$Executable = Join-Path $ProjectRoot 'dist\GRANDEAlpha\GRANDEAlpha.exe'
if (-not (Test-Path -LiteralPath $Executable)) {
    throw "PyInstaller completed without producing $Executable"
}
$Distribution = Split-Path -Parent $Executable
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'LICENSE') -Destination $Distribution
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'NOTICE') -Destination $Distribution
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'PRIVACY.md') -Destination $Distribution
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'SECURITY.md') -Destination $Distribution
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'README.md') -Destination $Distribution
Write-Host "Built: $ProjectRoot\dist\GRANDEAlpha\GRANDEAlpha.exe" -ForegroundColor Green
