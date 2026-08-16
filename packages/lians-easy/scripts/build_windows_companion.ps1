[CmdletBinding()]
param(
    [string]$OutputDirectory = "dist\companion",
    [switch]$Overwrite
)

$ErrorActionPreference = "Stop"
$packageRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $packageRoot "..\..")).Path
$outputRoot = if ([System.IO.Path]::IsPathRooted($OutputDirectory)) {
    [System.IO.Path]::GetFullPath($OutputDirectory)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $repositoryRoot $OutputDirectory))
}
$workRoot = Join-Path $outputRoot "pyinstaller-build"
$binaryRoot = Join-Path $outputRoot "pyinstaller-dist"
$companionPath = Join-Path $outputRoot "Lians.exe"

function Assert-UnderOutputRoot([string]$Path) {
    $resolved = [System.IO.Path]::GetFullPath($Path)
    $prefix = $outputRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
    if (-not $resolved.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to change a path outside the companion output directory: $resolved"
    }
}

New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null
foreach ($target in @($workRoot, $binaryRoot)) {
    Assert-UnderOutputRoot $target
    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Recurse -Force
    }
}
if (Test-Path -LiteralPath $companionPath) {
    Assert-UnderOutputRoot $companionPath
    if (-not $Overwrite) {
        throw "The companion EXE already exists. Pass -Overwrite to replace it."
    }
    Remove-Item -LiteralPath $companionPath -Force
}

python -m PyInstaller --version | Out-Null
python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --name LiansMemory `
    --exclude-module numpy `
    --paths $packageRoot `
    --add-data "$(Join-Path $packageRoot 'lians_easy/app');lians_easy/app" `
    --add-data "$(Join-Path $packageRoot 'lians_easy/desktop');lians_easy/desktop" `
    --icon (Join-Path $packageRoot "windows-lians.ico") `
    --version-file (Join-Path $packageRoot "windows-version-info.txt") `
    --workpath $workRoot `
    --distpath $binaryRoot `
    --specpath $workRoot `
    (Join-Path $packageRoot "entrypoint.py")
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$built = Join-Path $binaryRoot "LiansMemory.exe"
Copy-Item -LiteralPath $built -Destination $companionPath
$hash = (Get-FileHash -LiteralPath $companionPath -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath "$companionPath.sha256" -Encoding utf8 -Value "$hash  Lians.exe"

Write-Output $companionPath
Write-Output $hash
