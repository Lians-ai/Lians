param(
    [string]$Version = "0.4.0",
    [switch]$Overwrite
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../../..")).Path
$outputRoot = [System.IO.Path]::GetFullPath((Join-Path $repoRoot "dist/tester"))
$stageRoot = [System.IO.Path]::GetFullPath((Join-Path $outputRoot "Lians-Local-Preview-Windows"))
$buildRoot = [System.IO.Path]::GetFullPath((Join-Path $outputRoot "pyinstaller-build"))
$binaryRoot = [System.IO.Path]::GetFullPath((Join-Path $outputRoot "pyinstaller-dist"))
$zipPath = [System.IO.Path]::GetFullPath((Join-Path $outputRoot "Lians-Local-Preview-Windows-v$Version.zip"))

function Assert-UnderOutputRoot([string]$Path) {
    $resolved = [System.IO.Path]::GetFullPath($Path)
    $prefix = $outputRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
    if (-not $resolved.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to change a path outside the tester output directory: $resolved"
    }
}

New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null
foreach ($target in @($stageRoot, $buildRoot, $binaryRoot)) {
    Assert-UnderOutputRoot $target
    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Recurse -Force
    }
}
if (Test-Path -LiteralPath $zipPath) {
    Assert-UnderOutputRoot $zipPath
    if (-not $Overwrite) {
        throw "The ZIP already exists. Pass -Overwrite to replace it."
    }
    Remove-Item -LiteralPath $zipPath -Force
}

$brandingRoot = Join-Path $buildRoot "canonical-branding"
New-Item -ItemType Directory -Force -Path $brandingRoot | Out-Null
$faviconPath = Join-Path $brandingRoot "favicon.png"
$faviconBase64 = Get-Content -LiteralPath (Join-Path $repoRoot "packages/lians-easy/lians_easy/tester/favicon.png.b64") -Raw
[System.IO.File]::WriteAllBytes(
    $faviconPath,
    [Convert]::FromBase64String($faviconBase64.Trim())
)

python -m PyInstaller --version | Out-Null
python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name LiansPreview `
    --paths (Join-Path $repoRoot "packages/lians-easy") `
    --add-data "$(Join-Path $repoRoot 'packages/lians-easy/lians_easy/app');lians_easy/app" `
    --add-data "$(Join-Path $repoRoot 'packages/lians-easy/lians_easy/tester');lians_easy/tester" `
    --icon $faviconPath `
    --version-file (Join-Path $repoRoot "packages/lians-easy/windows-tester-version-info.txt") `
    --workpath $buildRoot `
    --distpath $binaryRoot `
    --specpath $buildRoot `
    (Join-Path $repoRoot "packages/lians-easy/tester_entrypoint.py")
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

New-Item -ItemType Directory -Force -Path (Join-Path $stageRoot "assets") | Out-Null
Copy-Item -LiteralPath (Join-Path $binaryRoot "LiansPreview.exe") -Destination $stageRoot
Copy-Item -LiteralPath (Join-Path $repoRoot "packages/lians-easy/tester-package/START-HERE.html") -Destination $stageRoot
Copy-Item -LiteralPath (Join-Path $repoRoot "packages/lians-easy/lians_easy/app/fonts/sora-latin.woff2") -Destination (Join-Path $stageRoot "assets")
Copy-Item -LiteralPath (Join-Path $repoRoot "packages/lians-easy/lians_easy/app/logo-blue.png") -Destination (Join-Path $stageRoot "assets")
Copy-Item -LiteralPath $faviconPath -Destination (Join-Path $stageRoot "assets")

$exePath = Join-Path $stageRoot "LiansPreview.exe"
$exeHash = (Get-FileHash -LiteralPath $exePath -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath (Join-Path $stageRoot "SHA256.txt") -Encoding utf8 -Value "$exeHash  LiansPreview.exe"
$commit = (git -C $repoRoot rev-parse HEAD).Trim()
$buildInfo = [ordered]@{
    package_version = $Version
    product_version = "0.5.0"
    commit = $commit
    built_at_utc = [DateTime]::UtcNow.ToString("o")
    platform = "Windows x64"
    signing = "unsigned technical preview"
    executable_sha256 = $exeHash
}
$buildInfo | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $stageRoot "BUILD-INFO.json") -Encoding utf8

Compress-Archive -LiteralPath $stageRoot -DestinationPath $zipPath -CompressionLevel Optimal
$zipHash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath "$zipPath.sha256" -Encoding utf8 -Value "$zipHash  $(Split-Path $zipPath -Leaf)"

Write-Output $zipPath
Write-Output $zipHash
