param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("hook", "prewarm")]
    [string]$Action
)

$ErrorActionPreference = "Stop"

# Plugin v0.1 intentionally uses the native per-user data home. Keeping the
# interpreter outside the active repository prevents a project-local uv.exe or
# python.exe from intercepting trusted hook stdin on Windows.
Remove-Item Env:LIANS_MEMORY_HOME -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
$env:PYTHONNOUSERSITE = "1"
$env:PYTHONSAFEPATH = "1"

function Test-FullyQualifiedNativePath([string]$Candidate) {
    if ([string]::IsNullOrWhiteSpace($Candidate)) {
        return $false
    }
    return (
        $Candidate -match '^[A-Za-z]:[\\/]' -or
        $Candidate -match '^\\\\[^\\/]+[\\/][^\\/]+'
    )
}

if (Test-FullyQualifiedNativePath $env:LOCALAPPDATA) {
    $nativeBase = $env:LOCALAPPDATA
} elseif (Test-FullyQualifiedNativePath $env:USERPROFILE) {
    $nativeBase = Join-Path $env:USERPROFILE "AppData\Local"
} else {
    exit 0
}
$dataHome = Join-Path $nativeBase "Lians\CodexMemory"
$python = Join-Path $dataHome "venv\Scripts\python.exe"
$launcher = Join-Path $PSScriptRoot "lians_plugin.py"

# Before first-run setup the hook must remain silent and fail open.
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    exit 0
}

if ($Action -eq "hook") {
    # PowerShell consumes redirected stdin as its own pipeline input. Read it
    # explicitly and forward it to the trusted runtime; otherwise Codex's
    # UserPromptSubmit JSON never reaches the Python hook.
    $payload = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($payload)) {
        exit 0
    }
    $payload | & $python -B $launcher $Action
} else {
    & $python -B $launcher $Action
}
exit $LASTEXITCODE
