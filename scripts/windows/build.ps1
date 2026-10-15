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
$BuildOutput = Join-Path $ProjectRoot 'build\pyinstaller'
$DistributionOutput = Join-Path $ProjectRoot 'dist'
$SpecOutput = Join-Path $ProjectRoot 'artifacts\pyinstaller'

$TemporaryRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$StageRoot = Join-Path $TemporaryRoot ("ga-pyi-$([guid]::NewGuid().ToString('N'))")
$SavedPipEnvironment = @{}
foreach ($Setting in Get-ChildItem Env: | Where-Object { $_.Name -like 'PIP_*' }) {
    $SavedPipEnvironment[$Setting.Name] = $Setting.Value
}
try {
    # Redirects and private indexes from the caller must not escape the build environment.
    foreach ($SettingName in $SavedPipEnvironment.Keys) { Remove-Item -LiteralPath "Env:$SettingName" }
    $env:PIP_CONFIG_FILE = 'NUL'
    # PyInstaller cannot resolve setuptools' editable scripts/ -> grande_alpha mapping.
    New-Item -ItemType Directory -Path $StageRoot | Out-Null
    # Keep shared Python packages and unrelated keyring plugins out of the executable.
    # PySide6's nested QML files must fit Windows' default path-length limit.
    $BuildEnvironment = Join-Path $StageRoot 'v'
    & $PythonExe -I -m venv $BuildEnvironment
    if ($LASTEXITCODE -ne 0) { throw 'The isolated packaging environment could not be created.' }
    $BuildPython = Join-Path $BuildEnvironment 'Scripts\python.exe'
    & $BuildPython -I -m pip install --disable-pip-version-check --upgrade pip 'setuptools>=83' wheel
    if ($LASTEXITCODE -ne 0) { throw 'The isolated packaging tools could not be installed.' }
    # Windows PowerShell 5.1 strips embedded quotes in native -c arguments.
    $BuildRequirementsJson = & $PythonExe -I (Join-Path $PSScriptRoot 'build_requirements.py') (Join-Path $ProjectRoot 'pyproject.toml')
    if ($LASTEXITCODE -ne 0) { throw 'The packaging dependency configuration could not be read.' }
    $BuildRequirements = @($BuildRequirementsJson | ConvertFrom-Json)
    & $BuildPython -I -m pip install --disable-pip-version-check "${ProjectRoot}[desktop]" @BuildRequirements
    if ($LASTEXITCODE -ne 0) { throw 'The declared packaging dependencies could not be installed.' }
    & $BuildPython -I (Join-Path $ProjectRoot 'tools\verify_worker_recovery.py')
    if ($LASTEXITCODE -ne 0) { throw 'Worker recovery acceptance failed in the isolated packaging environment.' }
    & $BuildPython -I (Join-Path $ProjectRoot 'tools\verify_agent_desk.py')
    if ($LASTEXITCODE -ne 0) { throw 'Agent desk acceptance failed in the isolated packaging environment.' }
    & $BuildPython -I (Join-Path $ProjectRoot 'tools\verify_session_setup.py')
    if ($LASTEXITCODE -ne 0) { throw 'Session setup acceptance failed in the isolated packaging environment.' }
    & $BuildPython -I (Join-Path $ProjectRoot 'tools\verify_local_ai.py')
    if ($LASTEXITCODE -ne 0) { throw 'Optional AI acceptance failed in the isolated packaging environment.' }
    & $BuildPython -I (Join-Path $ProjectRoot 'tools\verify_strategy_selection.py')
    if ($LASTEXITCODE -ne 0) { throw 'Strategy comparison acceptance failed in the isolated packaging environment.' }
    & $BuildPython -I (Join-Path $ProjectRoot 'tools\verify_broker_connection.py')
    if ($LASTEXITCODE -ne 0) { throw 'Broker connection acceptance failed in the isolated packaging environment.' }
    New-Item -ItemType Directory -Path $BuildOutput, $DistributionOutput, $SpecOutput -Force | Out-Null
    & $BuildPython -I -m pip freeze --all --exclude grande-alpha | Set-Content -LiteralPath (Join-Path $SpecOutput 'resolved-requirements.txt') -Encoding UTF8
    if ($LASTEXITCODE -ne 0) { throw 'The resolved packaging dependencies could not be recorded.' }
    Copy-Item -LiteralPath (Join-Path $ProjectRoot 'scripts') -Destination (Join-Path $StageRoot 'grande_alpha') -Recurse
    $SavedAnalysisPath = $env:PATH
    try {
        # Native dependency lookup must not collect unrelated DLLs from caller PATH
        # (for example Poppler ICU, which is incompatible with Qt's Windows ICU shim).
        $env:PATH = @(
            (Split-Path -Parent $BuildPython),
            (Split-Path -Parent $PythonExe),
            (Join-Path $env:WINDIR 'System32'),
            $env:WINDIR
        ) -join [IO.Path]::PathSeparator
    & $BuildPython -I -m PyInstaller --noconfirm --clean --windowed --name GRANDEAlpha `
        --workpath $BuildOutput --distpath $DistributionOutput --specpath $SpecOutput `
        --paths $StageRoot `
        --icon $IconIco --add-data "${IconPng};grande_alpha/assets" `
        --collect-all keyring `
        --collect-submodules grande_alpha `
        --collect-data rfc3987_syntax `
        --exclude-module matplotlib `
        --exclude-module IPython --exclude-module black `
        --exclude-module nbformat --exclude-module tkinter `
        --hidden-import mcp.client.auth.oauth2 --hidden-import mcp.client.streamable_http `
        --hidden-import mcp.shared.auth `
        (Join-Path $ProjectRoot 'scripts\app.py')
    $BuildExitCode = $LASTEXITCODE
    } finally {
        $env:PATH = $SavedAnalysisPath
    }
    if ($BuildExitCode -ne 0) { throw "PyInstaller failed with exit code $BuildExitCode" }
    $DesktopReport = Join-Path $StageRoot 'desktop-check.json'
    $DesktopCheckProcess = Start-Process -FilePath (Join-Path $DistributionOutput 'GRANDEAlpha\GRANDEAlpha.exe') `
        -ArgumentList '--check-desktop', '--report', "`"$DesktopReport`"" -WindowStyle Hidden -PassThru
    if (-not $DesktopCheckProcess.WaitForExit(30000)) {
        $DesktopCheckProcess.Kill()
        throw 'The packaged desktop startup check timed out.'
    }
    if ($DesktopCheckProcess.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $DesktopReport)) {
        if (Test-Path -LiteralPath $DesktopReport) { Get-Content -LiteralPath $DesktopReport }
        throw 'The packaged desktop could not start. Do not install or distribute this build.'
    }
    $DesktopResult = Get-Content -LiteralPath $DesktopReport -Raw | ConvertFrom-Json
    if ($DesktopResult.ok -ne $true) { throw 'The packaged desktop check did not confirm success.' }
    Write-Host 'Packaged desktop startup passed with disposable data; no worker, broker, or AI request.'
    & $BuildPython -I -m pip_audit --strict --progress-spinner off --path (Join-Path $DistributionOutput 'GRANDEAlpha\_internal')
    if ($LASTEXITCODE -ne 0) { throw 'The packaged dependency audit failed. Do not distribute this build.' }
} finally {
    foreach ($Setting in Get-ChildItem Env: | Where-Object { $_.Name -like 'PIP_*' }) {
        Remove-Item -LiteralPath "Env:$($Setting.Name)"
    }
    foreach ($SettingName in $SavedPipEnvironment.Keys) {
        Set-Item -LiteralPath "Env:$SettingName" -Value $SavedPipEnvironment[$SettingName]
    }
    $ResolvedStage = [IO.Path]::GetFullPath($StageRoot)
    if ($ResolvedStage.StartsWith($TemporaryRoot, [StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path -Leaf $ResolvedStage) -match '^ga-pyi-[0-9a-f]{32}$' -and
        (Test-Path -LiteralPath $ResolvedStage -PathType Container) -and
        -not ((Get-Item -LiteralPath $ResolvedStage -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
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
$Executable = Join-Path $DistributionOutput 'GRANDEAlpha\GRANDEAlpha.exe'
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
