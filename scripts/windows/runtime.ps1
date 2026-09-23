function Get-GrandeAlphaRuntimeRoot([string]$ProjectRoot) {
    $LocalRuntime = Join-Path $ProjectRoot '.venv'
    if (Test-Path -LiteralPath (Join-Path $LocalRuntime 'Scripts\python.exe')) {
        return $LocalRuntime
    }
    if ($env:GRANDE_ALPHA_RUNTIME_DIR) {
        return [IO.Path]::GetFullPath($env:GRANDE_ALPHA_RUNTIME_DIR)
    }
    return Join-Path $env:LOCALAPPDATA 'GRANDEAlpha\runtime'
}

function Get-GrandeAlphaPython([string]$ProjectRoot, [switch]$Windowed) {
    $RuntimeRoot = Get-GrandeAlphaRuntimeRoot $ProjectRoot
    $Executable = if ($Windowed) { 'pythonw.exe' } else { 'python.exe' }
    return Join-Path $RuntimeRoot "Scripts\$Executable"
}
