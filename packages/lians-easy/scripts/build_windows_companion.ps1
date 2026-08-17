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
$runtimeRoot = Join-Path $outputRoot "runtime-dist"
$windowRoot = Join-Path $outputRoot "window-dist"
$appBundle = Join-Path $outputRoot "LiansApp"
$companionPath = Join-Path $outputRoot "Lians.exe"

function Assert-UnderOutputRoot([string]$Path) {
    $resolved = [System.IO.Path]::GetFullPath($Path)
    $prefix = $outputRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
    if (-not $resolved.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to change a path outside the companion output directory: $resolved"
    }
}

New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null
foreach ($target in @($workRoot, $runtimeRoot, $windowRoot, $appBundle)) {
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

# MCP keeps a console-subsystem sidecar because AI clients communicate with it
# over stdin/stdout. The human-facing executable never launches that console.
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
    --workpath (Join-Path $workRoot "runtime") `
    --distpath $runtimeRoot `
    --specpath (Join-Path $workRoot "runtime-spec") `
    (Join-Path $packageRoot "entrypoint.py")
if ($LASTEXITCODE -ne 0) {
    throw "Runtime PyInstaller build failed with exit code $LASTEXITCODE"
}

# Onedir removes PyInstaller extraction from every human launch. --windowed
# removes the PowerShell/console flash before Python begins importing modules.
python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --windowed `
    --name Lians `
    --exclude-module numpy `
    --exclude-module PyQt5 `
    --exclude-module PyQt6 `
    --exclude-module PySide2 `
    --exclude-module PySide6 `
    --exclude-module cefpython3 `
    --paths $packageRoot `
    --add-data "$(Join-Path $packageRoot 'lians_easy/app');lians_easy/app" `
    --add-data "$(Join-Path $packageRoot 'lians_easy/desktop');lians_easy/desktop" `
    --icon (Join-Path $packageRoot "windows-lians.ico") `
    --version-file (Join-Path $packageRoot "windows-companion-version-info.txt") `
    --workpath (Join-Path $workRoot "window") `
    --distpath $windowRoot `
    --specpath (Join-Path $workRoot "window-spec") `
    (Join-Path $packageRoot "companion_entrypoint.py")
if ($LASTEXITCODE -ne 0) {
    throw "Windowed PyInstaller build failed with exit code $LASTEXITCODE"
}

Copy-Item -LiteralPath (Join-Path $windowRoot "Lians") -Destination $appBundle -Recurse
Copy-Item -LiteralPath (Join-Path $runtimeRoot "LiansMemory.exe") -Destination (Join-Path $appBundle "LiansMemory.exe")

$compiler = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path -LiteralPath $compiler)) {
    throw "The Windows launcher compiler is unavailable: $compiler"
}
& $compiler `
    /nologo `
    /target:winexe `
    /optimize+ `
    /reference:System.Drawing.dll `
    /reference:System.Windows.Forms.dll `
    "/win32icon:$(Join-Path $packageRoot 'windows-lians.ico')" `
    "/out:$companionPath" `
    (Join-Path $packageRoot "windows-launcher.cs")
if ($LASTEXITCODE -ne 0) {
    throw "Launcher build failed with exit code $LASTEXITCODE"
}

$hash = (Get-FileHash -LiteralPath $companionPath -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath "$companionPath.sha256" -Encoding utf8 -Value "$hash  Lians.exe"

Write-Output $companionPath
Write-Output (Join-Path $appBundle "Lians.exe")
Write-Output $hash
