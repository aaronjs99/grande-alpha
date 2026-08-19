param(
    [string]$PythonExecutable = ''
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ProjectRoot 'runtime-path.ps1')
$ConfiguredPython = if ($PythonExecutable) {
    [IO.Path]::GetFullPath($PythonExecutable)
} else {
    Get-GrandeAlphaPython $ProjectRoot
}
$PythonExe = if (Test-Path -LiteralPath $ConfiguredPython) { $ConfiguredPython } else { 'python' }
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
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'SUPPORT.md') -Destination $Distribution
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'CODE_OF_CONDUCT.md') -Destination $Distribution
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'CONTRIBUTING.md') -Destination $Distribution
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'README.md') -Destination $Distribution
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'docs') -Destination $Distribution -Recurse -Force
Write-Host "Built: $ProjectRoot\dist\GRANDEAlpha\GRANDEAlpha.exe" -ForegroundColor Green
