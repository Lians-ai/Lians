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
try {
    $dataHome = Join-Path $nativeBase "Lians\CodexMemory"
    $python = Join-Path $dataHome "venv\Scripts\python.exe"
    $launcher = Join-Path $PSScriptRoot "lians_plugin.py"
    $pythonReady = Test-Path -LiteralPath $python -PathType Leaf
} catch {
    # Invalid or hostile native-home values must remain silent and fail open.
    exit 0
}

# Before first-run setup the hook must remain silent and fail open.
if (-not $pythonReady) {
    exit 0
}

if ($Action -eq "hook") {
    # Forward the redirected pipe as bytes. Windows PowerShell 5.1 otherwise
    # decodes UTF-8 through its console code page before Python sees it.
    $payloadStream = New-Object System.IO.MemoryStream
    try {
        [Console]::OpenStandardInput().CopyTo($payloadStream)
        $payloadBytes = $payloadStream.ToArray()
    } finally {
        $payloadStream.Dispose()
    }
    if ($payloadBytes.Length -eq 0) {
        exit 0
    }
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $python
    $startInfo.Arguments = '-B "' + $launcher + '" ' + $Action
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardInput = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    try {
        [void]$process.Start()
        $process.StandardInput.BaseStream.Write($payloadBytes, 0, $payloadBytes.Length)
        $process.StandardInput.BaseStream.Flush()
        $process.StandardInput.Close()
        $process.WaitForExit()
        $exitCode = $process.ExitCode
    } finally {
        $process.Dispose()
    }
    exit $exitCode
} else {
    & $python -B $launcher $Action
}
exit $LASTEXITCODE
