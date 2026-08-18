[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$Python,
  [Parameter(Mandatory = $true)]
  [string]$ExtractionRoot,
  [string]$EnvironmentFile,
  [string]$PathFile
)

$ErrorActionPreference = 'Stop'

$pythonVersion = '3.12.10'
$msiUri = "https://www.python.org/ftp/python/$pythonVersion/amd64/tcltk.msi"
$msiSha256 = '55c96ffad69b1c834aa52e11b9ce41637a178ba6ad6607e83956044834276e2a'
$expectedSigner = 'CN=Python Software Foundation, O=Python Software Foundation, L=Beaverton, S=Oregon, C=US'
$downloadRoot = if ([string]::IsNullOrWhiteSpace($env:RUNNER_TEMP)) {
  [System.IO.Path]::GetTempPath()
} else {
  $env:RUNNER_TEMP
}
$msi = Join-Path $downloadRoot "python-$pythonVersion-tcltk-amd64.msi"

$pythonFile = Get-Item -LiteralPath $Python
$pythonRoot = $pythonFile.Directory.FullName
if ((& $pythonFile.FullName -c 'import sys; print(".".join(map(str, sys.version_info[:3])))').Trim() -ne $pythonVersion) {
  throw "The Windows build must use Python $pythonVersion"
}

Invoke-WebRequest -UseBasicParsing -Uri $msiUri -OutFile $msi

$actualSha256 = (Get-FileHash -LiteralPath $msi -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualSha256 -ne $msiSha256) {
  throw "Python Tcl/Tk MSI SHA-256 was $actualSha256; expected $msiSha256"
}

$signature = Get-AuthenticodeSignature -LiteralPath $msi
if ($signature.Status -ne 'Valid') {
  throw "Python Tcl/Tk MSI Authenticode status was $($signature.Status)"
}
if ($signature.SignerCertificate.Subject -ne $expectedSigner) {
  throw "Python Tcl/Tk MSI signer was not the Python Software Foundation"
}

if (Test-Path -LiteralPath $ExtractionRoot) {
  throw "Refusing to overwrite existing Tcl/Tk extraction root $ExtractionRoot"
}
$runtimeRoot = "$ExtractionRoot-runtime"
if (Test-Path -LiteralPath $runtimeRoot) {
  throw "Refusing to overwrite existing Windows build runtime $runtimeRoot"
}
$arguments = @('/a', "`"$msi`"", '/qn', "TARGETDIR=`"$ExtractionRoot`"")
$extract = Start-Process -FilePath msiexec.exe -ArgumentList $arguments -Wait -PassThru -NoNewWindow
if ($extract.ExitCode -ne 0) {
  throw "Python Tcl/Tk MSI extraction exited with code $($extract.ExitCode)"
}

$sourceTcl = Join-Path $ExtractionRoot 'tcl'
$sourceTclLibrary = Join-Path $sourceTcl 'tcl8.6'
$sourceTkLibrary = Join-Path $sourceTcl 'tk8.6'
$sourceInit = Join-Path $sourceTclLibrary 'init.tcl'
$sourceButton = Join-Path $sourceTcl 'tk8.6\ttk\button.tcl'
if (-not (Test-Path -LiteralPath $sourceInit -PathType Leaf)) {
  throw 'The verified Python Tcl/Tk MSI is missing tcl8.6/init.tcl'
}
if (-not (Test-Path -LiteralPath $sourceButton -PathType Leaf)) {
  throw 'The verified Python Tcl/Tk MSI is missing ttk/button.tcl'
}

# Clone the pinned Python core, then overlay the complete official Tcl/Tk MSI.
# This avoids mixing a mutable setup-python cache with a different _tkinter
# binary or Tcl/Tk DLL while keeping the source runtime untouched.
New-Item -ItemType Directory -Path $runtimeRoot | Out-Null
$excludedDirectories = @(
  (Join-Path $pythonRoot 'Lib\site-packages'),
  (Join-Path $pythonRoot 'Scripts'),
  (Join-Path $pythonRoot 'tcl')
)
& robocopy.exe $pythonRoot $runtimeRoot /E /XD $excludedDirectories /NFL /NDL /NJH /NJS /NP
if ($LASTEXITCODE -ge 8) {
  throw "Cloning the pinned Windows Python runtime failed with robocopy code $LASTEXITCODE"
}
& robocopy.exe $ExtractionRoot $runtimeRoot /E /NFL /NDL /NJH /NJS /NP
if ($LASTEXITCODE -ge 8) {
  throw "Overlaying the verified Tcl/Tk runtime failed with robocopy code $LASTEXITCODE"
}

$runtimePython = Join-Path $runtimeRoot 'python.exe'
$runtimeTclLibrary = Join-Path $runtimeRoot 'tcl\tcl8.6'
$runtimeTkLibrary = Join-Path $runtimeRoot 'tcl\tk8.6'
$requiredRuntimeFiles = @(
  $runtimePython,
  (Join-Path $runtimeRoot 'DLLs\_tkinter.pyd'),
  (Join-Path $runtimeRoot 'DLLs\tcl86t.dll'),
  (Join-Path $runtimeRoot 'DLLs\tk86t.dll'),
  (Join-Path $runtimeTclLibrary 'init.tcl'),
  (Join-Path $runtimeTkLibrary 'ttk\button.tcl')
)
foreach ($requiredFile in $requiredRuntimeFiles) {
  if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
    throw "The isolated Windows build runtime is missing $requiredFile"
  }
}

& $runtimePython -m ensurepip --upgrade
if ($LASTEXITCODE -ne 0) {
  throw 'The isolated Windows build Python could not bootstrap pip'
}

$env:TCL_LIBRARY = $runtimeTclLibrary
$env:TK_LIBRARY = $runtimeTkLibrary
$env:LIANS_BUILD_PYTHON = $runtimePython
if (-not [string]::IsNullOrWhiteSpace($EnvironmentFile)) {
  "TCL_LIBRARY=$runtimeTclLibrary" | Out-File -LiteralPath $EnvironmentFile -Encoding utf8 -Append
  "TK_LIBRARY=$runtimeTkLibrary" | Out-File -LiteralPath $EnvironmentFile -Encoding utf8 -Append
  "LIANS_BUILD_PYTHON=$runtimePython" | Out-File -LiteralPath $EnvironmentFile -Encoding utf8 -Append
}
if (-not [string]::IsNullOrWhiteSpace($PathFile)) {
  $runtimeRoot | Out-File -LiteralPath $PathFile -Encoding utf8 -Append
  (Join-Path $runtimeRoot 'Scripts') | Out-File -LiteralPath $PathFile -Encoding utf8 -Append
}

& $runtimePython -c 'import tkinter as tk; root = tk.Tk(); root.withdraw(); root.update_idletasks(); root.destroy()'
if ($LASTEXITCODE -ne 0) {
  throw 'The Windows build Python could not initialize the verified Tcl/Tk runtime'
}
