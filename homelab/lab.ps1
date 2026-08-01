[CmdletBinding()]
param(
    [ValidateSet("up", "up-real", "check-sample", "verify", "verify-real", "status", "logs", "logs-real", "proof", "report", "down", "dispose", "reset")]
    [string]$Command = "up",
    [string]$SamplePath,
    [switch]$AcceptSamplePolicy,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$LabRoot = $PSScriptRoot
$ComposeFile = Join-Path $LabRoot "compose.yaml"
$RealModelFile = Join-Path $LabRoot "compose.real-model.yaml"
$EnvFile = Join-Path $LabRoot ".env"
$ExampleEnv = Join-Path $LabRoot ".env.example"
$Artifacts = Join-Path $LabRoot "artifacts"
$DefaultSample = Join-Path $LabRoot "samples\default.json"
$SamplePolicyAck = "I_CONFIRM_THIS_SAMPLE_IS_DEIDENTIFIED"
$HadSampleFile = Test-Path Env:LAB_SAMPLE_FILE
$PreviousSampleFile = $env:LAB_SAMPLE_FILE
$HadSamplePolicyAck = Test-Path Env:LAB_SAMPLE_POLICY_ACK
$PreviousSamplePolicyAck = $env:LAB_SAMPLE_POLICY_ACK
$HadGitCommit = Test-Path Env:LAB_GIT_COMMIT
$PreviousGitCommit = $env:LAB_GIT_COMMIT

function Set-LabSample {
    $candidate = if ($SamplePath) { $SamplePath } else { $DefaultSample }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        throw "Python 3 is required for fail-closed sample validation."
    }
    $resolved = (& $python.Source (Join-Path $LabRoot "workload\scenario.py") `
        --resolve-for-launch $candidate $LabRoot 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $resolved) {
        throw "Sample file could not be resolved within the local sample policy."
    }
    $file = Get-Item -LiteralPath ([string]$resolved).Trim()
    if (-not $file.PSIsContainer -and $file.Length -gt 0 -and $file.Length -le 65536) {
        $env:LAB_SAMPLE_FILE = $file.FullName
    }
    else {
        throw "Sample must be a non-empty JSON file no larger than 64 KiB."
    }
    if ($AcceptSamplePolicy) {
        $env:LAB_SAMPLE_POLICY_ACK = $SamplePolicyAck
    }
    else {
        Remove-Item Env:LAB_SAMPLE_POLICY_ACK -ErrorAction SilentlyContinue
    }
}

function Test-LabSample {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        throw "Python 3 is required for fail-closed sample validation."
    }
    & $python.Source (Join-Path $LabRoot "workload\scenario.py") $env:LAB_SAMPLE_FILE
    if ($LASTEXITCODE -ne 0) {
        throw "Sample validation failed. No containers were started."
    }
}

function Get-LabRevision {
    $repoRoot = Split-Path -Parent $LabRoot
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { return "unrecorded" }
    $commit = (& git -C $repoRoot rev-parse --verify HEAD 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $commit) { return "unrecorded" }
    $dirty = (& git -C $repoRoot status --porcelain --untracked-files=normal 2>$null)
    if ($dirty) { return "$($commit.Trim())-dirty" }
    return $commit.Trim()
}

function New-LabEnvironment {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        throw "Python 3 is required once to generate local lab secrets."
    }
    $output = @(& $python.Source (Join-Path $LabRoot "env_bootstrap.py") $ExampleEnv $EnvFile)
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create or safely upgrade homelab/.env."
    }
    $status = ([string]($output | Select-Object -Last 1)).Trim()
    switch ($status) {
        "created" { Write-Host "Created homelab/.env with random local secrets." }
        "upgraded" { Write-Host "Added a random Evidence Pack signing key to the existing homelab/.env." }
        "unchanged" { }
        default { throw "Unexpected homelab environment bootstrap result." }
    }
}

function Assert-DockerEngine {
    & docker info *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Desktop is installed but its Linux engine is not running. Start Docker Desktop, then retry."
    }
}

function Invoke-Compose {
    param(
        [string[]]$Arguments,
        [switch]$RealModel
    )
    $base = @("compose", "--env-file", $EnvFile, "-f", $ComposeFile)
    if ($RealModel) {
        $base += @("--project-name", "lians-homelab-real", "-f", $RealModelFile)
    }
    & docker @base @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose failed with exit code $LASTEXITCODE"
    }
}

function Invoke-Verification {
    param([switch]$RealModel)
    Invoke-Compose -RealModel:$RealModel -Arguments @("--profile", "tools", "build", "verify")
    Invoke-Compose -RealModel:$RealModel -Arguments @("--profile", "tools", "run", "--rm", "verify")
}

function Show-Endpoints {
    Write-Host ""
    Write-Host "Lians API   http://localhost:8001/docs"
    Write-Host "Grafana     http://localhost:3000/d/lians-homelab-proof"
    Write-Host "Prometheus  http://localhost:9090"
    Write-Host "Alloy       http://localhost:12345"
    Write-Host ""
    Write-Host "Grafana credentials are in homelab/.env."
}

try {
if ($Command -in @("proof", "report")) {
    $latestProof = Join-Path $Artifacts "latest-receipt.json"
    if (-not (Test-Path -LiteralPath $latestProof)) {
        throw "No exported proof exists yet. Run '.\lab.ps1 up' first."
    }
    Get-Content -Raw -LiteralPath $latestProof
    return
}

Set-LabSample

if ($Command -eq "check-sample") {
    Test-LabSample
    return
}

New-LabEnvironment
New-Item -ItemType Directory -Force -Path $Artifacts | Out-Null
$env:LAB_GIT_COMMIT = Get-LabRevision
Assert-DockerEngine

switch ($Command) {
    "up" {
        Test-LabSample
        Invoke-Compose -RealModel -Arguments @("down", "--remove-orphans")
        Invoke-Compose -Arguments @("up", "--build", "-d")
        Invoke-Verification
        Show-Endpoints
    }
    "up-real" {
        Test-LabSample
        Invoke-Compose -Arguments @("down", "--remove-orphans")
        Invoke-Compose -RealModel -Arguments @("up", "--build", "-d")
        Invoke-Verification -RealModel
        Show-Endpoints
    }
    "verify" {
        Invoke-Verification
    }
    "verify-real" {
        Invoke-Verification -RealModel
    }
    "status" {
        Write-Host "Lightweight project"
        Invoke-Compose -Arguments @("ps")
        Write-Host "Real-model project"
        Invoke-Compose -RealModel -Arguments @("ps")
        Show-Endpoints
    }
    "logs" {
        Invoke-Compose -Arguments @("logs", "--follow", "--tail", "200")
    }
    "logs-real" {
        Invoke-Compose -RealModel -Arguments @("logs", "--follow", "--tail", "200")
    }
    "down" {
        Invoke-Compose -Arguments @("down", "--remove-orphans")
        Invoke-Compose -RealModel -Arguments @("down", "--remove-orphans")
    }
    { $_ -in @("dispose", "reset") } {
        if (-not $Force) {
            $answer = Read-Host "Dispose deletes all homelab databases, telemetry, and proof state. Type DISPOSE to continue"
            if ($answer -ne "DISPOSE") { throw "Dispose cancelled." }
        }
        Invoke-Compose -Arguments @("down", "--volumes", "--remove-orphans")
        Invoke-Compose -RealModel -Arguments @("down", "--volumes", "--remove-orphans")
        Write-Host "Removed the homelab containers and named volumes. homelab/.env and sanitized exported reports were preserved."
    }
}
}
finally {
    if ($HadSampleFile) { $env:LAB_SAMPLE_FILE = $PreviousSampleFile }
    else { Remove-Item Env:LAB_SAMPLE_FILE -ErrorAction SilentlyContinue }
    if ($HadSamplePolicyAck) { $env:LAB_SAMPLE_POLICY_ACK = $PreviousSamplePolicyAck }
    else { Remove-Item Env:LAB_SAMPLE_POLICY_ACK -ErrorAction SilentlyContinue }
    if ($HadGitCommit) { $env:LAB_GIT_COMMIT = $PreviousGitCommit }
    else { Remove-Item Env:LAB_GIT_COMMIT -ErrorAction SilentlyContinue }
}
