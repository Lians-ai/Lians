[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$Python,
  [Parameter(Mandatory = $true)]
  [string]$ExtractionRoot,
  [string]$EnvironmentFile
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

# Keep the checksum- and signature-verified extraction separate from the
# mutable setup-python tool cache. Explicit library paths make every later
# process in the job use the same files that are validated below.
$env:TCL_LIBRARY = $sourceTclLibrary
$env:TK_LIBRARY = $sourceTkLibrary
if (-not [string]::IsNullOrWhiteSpace($EnvironmentFile)) {
  "TCL_LIBRARY=$sourceTclLibrary" | Out-File -LiteralPath $EnvironmentFile -Encoding utf8 -Append
  "TK_LIBRARY=$sourceTkLibrary" | Out-File -LiteralPath $EnvironmentFile -Encoding utf8 -Append
}

& $pythonFile.FullName -c 'import tkinter as tk; root = tk.Tk(); root.withdraw(); root.update_idletasks(); root.destroy()'
if ($LASTEXITCODE -ne 0) {
  throw 'The Windows build Python could not initialize the verified Tcl/Tk runtime'
}
