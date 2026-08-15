[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Binary,

    [string]$OutputDirectory = "dist\installer",

    [string]$Version
)

$ErrorActionPreference = "Stop"
$packageRoot = Split-Path -Parent $PSScriptRoot
$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $packageRoot "..\..")).Path
$binaryPath = (Resolve-Path -LiteralPath $Binary).Path
$iconPath = (Resolve-Path -LiteralPath (Join-Path $packageRoot "windows-lians.ico")).Path
$scriptPath = (Resolve-Path -LiteralPath (Join-Path $packageRoot "windows-installer.nsi")).Path

if (-not $Version) {
    $versionLine = Select-String -LiteralPath (Join-Path $packageRoot "pyproject.toml") -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
    if (-not $versionLine -or $versionLine.Line -notmatch '^version\s*=\s*"([^"]+)"') {
        throw "Could not read the Lians Bridge package version"
    }
    $Version = $Matches[1]
}
if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    throw "Lians installer version must be stable semver without a prefix: $Version"
}

$compilerCommand = Get-Command makensis.exe -ErrorAction SilentlyContinue
if ($compilerCommand) {
    $compilerPath = $compilerCommand.Source
} else {
    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} "NSIS\makensis.exe"),
        (Join-Path $env:ProgramFiles "NSIS\makensis.exe")
    )
    $compilerPath = $candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
    if (-not $compilerPath) {
        throw "NSIS makensis.exe was not found. Install the pinned compiler before packaging."
    }
}

$outputRoot = if ([System.IO.Path]::IsPathRooted($OutputDirectory)) {
    $OutputDirectory
} else {
    Join-Path $repositoryRoot $OutputDirectory
}
New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null
$outputPath = Join-Path (Resolve-Path -LiteralPath $outputRoot).Path "Lians-Setup-$Version.exe"

$arguments = @(
    "/V4",
    "/DLIANS_VERSION=$Version",
    "/DLIANS_BINARY=$binaryPath",
    "/DLIANS_OUTPUT=$outputPath",
    "/DLIANS_ICON=$iconPath",
    $scriptPath
)
& $compilerPath @arguments
if ($LASTEXITCODE -ne 0) {
    throw "NSIS failed with exit code $LASTEXITCODE"
}
if (-not (Test-Path -LiteralPath $outputPath)) {
    throw "NSIS reported success but the installer was not created: $outputPath"
}

Get-Item -LiteralPath $outputPath | Select-Object FullName, Length, VersionInfo
