param(
    [string]$PythonExecutable = ''
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
. (Join-Path $PSScriptRoot 'runtime.ps1')
$ConfiguredPython = if ($PythonExecutable) {
    [IO.Path]::GetFullPath($PythonExecutable)
} else {
    Get-GrandeAlphaPython $ProjectRoot
}
$PythonExe = if (Test-Path -LiteralPath $ConfiguredPython) { $ConfiguredPython } else { 'python' }
$IconIco = Join-Path $ProjectRoot 'assets\brand\grande-alpha.ico'
$IconPng = Join-Path $ProjectRoot 'scripts\assets\app-icon.png'

$TemporaryRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$StageRoot = Join-Path $TemporaryRoot ("grande-alpha-pyinstaller-$([guid]::NewGuid().ToString('N'))")
try {
    # PyInstaller cannot resolve setuptools' editable scripts/ -> grande_alpha mapping.
    New-Item -ItemType Directory -Path $StageRoot | Out-Null
    Copy-Item -LiteralPath (Join-Path $ProjectRoot 'scripts') -Destination (Join-Path $StageRoot 'grande_alpha') -Recurse
    & $PythonExe -m PyInstaller --noconfirm --clean --windowed --name GRANDEAlpha `
        --paths $StageRoot `
        --icon $IconIco --add-data "${IconPng};grande_alpha/assets" `
        --collect-all keyring `
        --collect-submodules grande_alpha `
        --collect-data rfc3987_syntax `
        --exclude-module pyqtgraph --exclude-module matplotlib `
        --exclude-module IPython --exclude-module pytest --exclude-module black `
        --exclude-module nbformat --exclude-module tkinter `
        --hidden-import mcp.client.auth.oauth2 --hidden-import mcp.client.streamable_http `
        --hidden-import mcp.shared.auth `
        (Join-Path $ProjectRoot 'scripts\app.py')
    $BuildExitCode = $LASTEXITCODE
} finally {
    $ResolvedStage = [IO.Path]::GetFullPath($StageRoot)
    if ($ResolvedStage.StartsWith($TemporaryRoot, [StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path -Leaf $ResolvedStage) -like 'grande-alpha-pyinstaller-*') {
        try {
            Remove-Item -LiteralPath $ResolvedStage -Recurse -Force
        } catch {
            Write-Warning "Temporary packaging files remain at ${ResolvedStage}: $_"
        }
    }
}
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
