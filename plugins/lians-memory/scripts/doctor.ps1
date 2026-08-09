$ErrorActionPreference = "Stop"
$launcher = Join-Path $PSScriptRoot "lians_plugin.py"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Error "uv is required. Install it from https://docs.astral.sh/uv/ and rerun this command."
}

& uv run --managed-python --no-project --python 3.11 python -I -B $launcher doctor @args
exit $LASTEXITCODE
