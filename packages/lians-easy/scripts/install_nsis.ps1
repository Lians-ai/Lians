[CmdletBinding()]
param(
    [string]$Destination
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$nsisVersion = "3.12"
$archiveSha256 = "56581f90db321581c5381193d796fffcf2d24b2f8fed2160a6c6a3baa67f2c4f"
$downloadUrl = "https://downloads.sourceforge.net/project/nsis/NSIS%203/3.12/nsis-3.12.zip"

if (-not $Destination) {
    $ephemeralRoot = if ($env:RUNNER_TEMP) {
        $env:RUNNER_TEMP
    } else {
        [System.IO.Path]::GetTempPath()
    }
    $Destination = Join-Path $ephemeralRoot "lians-nsis-$nsisVersion"
}

$destinationRoot = [System.IO.Path]::GetFullPath($Destination)
New-Item -ItemType Directory -Force -Path $destinationRoot | Out-Null
$archivePath = Join-Path $destinationRoot "nsis-$nsisVersion.zip"

& curl.exe `
    --proto "=https" `
    --tlsv1.2 `
    --fail `
    --location `
    --retry 5 `
    --retry-delay 2 `
    --retry-all-errors `
    --output $archivePath `
    $downloadUrl
if ($LASTEXITCODE -ne 0) {
    throw "Official NSIS download failed with exit code $LASTEXITCODE"
}

$actualSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $archivePath).Hash.ToLowerInvariant()
if ($actualSha256 -ne $archiveSha256) {
    throw "NSIS archive SHA-256 was '$actualSha256'; expected '$archiveSha256'"
}

Expand-Archive -LiteralPath $archivePath -DestinationPath $destinationRoot -Force
$compilerRoot = Join-Path $destinationRoot "nsis-$nsisVersion"
$compilerPath = Join-Path $compilerRoot "makensis.exe"
if (-not (Test-Path -LiteralPath $compilerPath -PathType Leaf)) {
    throw "Verified NSIS archive did not contain makensis.exe"
}

$actualVersion = (& $compilerPath /VERSION).Trim()
if ($actualVersion -ne "v$nsisVersion") {
    throw "Expected NSIS v$nsisVersion; found '$actualVersion'"
}

$env:PATH = "$compilerRoot$([System.IO.Path]::PathSeparator)$env:PATH"
if ($env:GITHUB_PATH) {
    Add-Content -LiteralPath $env:GITHUB_PATH -Value $compilerRoot -Encoding utf8
}

[pscustomobject]@{
    Compiler = $compilerPath
    Version = $actualVersion
    Sha256 = $actualSha256
}
