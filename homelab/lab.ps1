[CmdletBinding()]
param(
    [ValidateSet("up", "up-real", "check-sample", "check-dataset", "generate-dataset", "ingest-dataset", "list-integrations", "verify", "verify-real", "status", "logs", "logs-real", "proof", "report", "capacity-report", "down", "dispose", "reset")]
    [string]$Command = "up",
    [string]$SamplePath,
    [string]$DatasetPath,
    [ValidateSet("laptop", "workstation", "dedicated")]
    [string]$ScaleProfile = "laptop",
    [ValidateRange(1, 10000000)]
    [int]$Records = 10000,
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
$DefaultDataset = Join-Path $LabRoot "datasets\default.ndjson"
$DefaultGeneratedDataset = Join-Path $LabRoot "datasets\generated.local.ndjson"
$IntegrationCatalog = Join-Path $LabRoot "integrations\catalog.json"
$SamplePolicyAck = "I_CONFIRM_THIS_SAMPLE_IS_DEIDENTIFIED"
$HadSampleFile = Test-Path Env:LAB_SAMPLE_FILE
$PreviousSampleFile = $env:LAB_SAMPLE_FILE
$HadSamplePolicyAck = Test-Path Env:LAB_SAMPLE_POLICY_ACK
$PreviousSamplePolicyAck = $env:LAB_SAMPLE_POLICY_ACK
$HadGitCommit = Test-Path Env:LAB_GIT_COMMIT
$PreviousGitCommit = $env:LAB_GIT_COMMIT
$DatasetEnvironmentNames = @(
    "LAB_DATASET_FILE",
    "LAB_DATASET_POLICY_ACK",
    "LAB_SCALE_PROFILE",
    "LAB_BULK_CONCURRENCY",
    "LAB_DATASET_MAX_RECORDS",
    "LAB_DATASET_MAX_BYTES",
    "LAB_DATASET_MAX_LINE_BYTES",
    "LAB_BULK_REQUEST_TIMEOUT_SECONDS",
    "LAB_RATE_LIMIT_PER_MINUTE"
)
$PreviousDatasetEnvironment = @{}
foreach ($name in $DatasetEnvironmentNames) {
    $PreviousDatasetEnvironment[$name] = @{
        Present = Test-Path "Env:$name"
        Value = [Environment]::GetEnvironmentVariable($name, "Process")
    }
}

function Get-LabPython {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        throw "Python 3 is required for fail-closed local input validation."
    }
    return $python.Source
}

function Set-LabScaleProfile {
    $profilePath = Join-Path $LabRoot "profiles\$ScaleProfile.env"
    if (-not (Test-Path -LiteralPath $profilePath -PathType Leaf)) {
        throw "Unknown or missing scale profile: $ScaleProfile"
    }
    $allowed = @(
        "LAB_SCALE_PROFILE",
        "LAB_BULK_CONCURRENCY",
        "LAB_DATASET_MAX_RECORDS",
        "LAB_DATASET_MAX_BYTES",
        "LAB_DATASET_MAX_LINE_BYTES",
        "LAB_BULK_REQUEST_TIMEOUT_SECONDS",
        "LAB_RATE_LIMIT_PER_MINUTE"
    )
    foreach ($line in Get-Content -LiteralPath $profilePath -Encoding utf8) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) { continue }
        if ($trimmed -notmatch '^([A-Z0-9_]+)=([A-Za-z0-9_-]+)$') {
            throw "Scale profile contains an invalid assignment."
        }
        $name = $Matches[1]
        $value = $Matches[2]
        if ($name -notin $allowed) {
            throw "Scale profile contains an unsupported setting."
        }
        [Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
    if ($env:LAB_SCALE_PROFILE -ne $ScaleProfile) {
        throw "Scale profile identity does not match its filename."
    }
}

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

function Set-LabDataset {
    $candidate = if ($DatasetPath) { $DatasetPath } else { $DefaultDataset }
    $python = Get-LabPython
    $resolved = (& $python (Join-Path $LabRoot "workload\dataset.py") `
        --resolve-for-launch $candidate $LabRoot 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $resolved) {
        throw "Dataset file could not be resolved within the local dataset policy."
    }
    $file = Get-Item -LiteralPath ([string]$resolved).Trim()
    if ($file.PSIsContainer -or $file.Length -le 0) {
        throw "Dataset must be a non-empty NDJSON file."
    }
    $env:LAB_DATASET_FILE = $file.FullName
    if ($AcceptSamplePolicy) {
        $env:LAB_DATASET_POLICY_ACK = $SamplePolicyAck
    }
    else {
        Remove-Item Env:LAB_DATASET_POLICY_ACK -ErrorAction SilentlyContinue
    }
}

function Test-LabDataset {
    $python = Get-LabPython
    & $python (Join-Path $LabRoot "workload\dataset.py") check $env:LAB_DATASET_FILE
    if ($LASTEXITCODE -ne 0) {
        throw "Dataset validation failed. No dataset records were written."
    }
}

function New-LabDataset {
    $python = Get-LabPython
    $candidate = if ($DatasetPath) { $DatasetPath } else { $DefaultGeneratedDataset }
    $target = [IO.Path]::GetFullPath($candidate)
    $repoRoot = [IO.Path]::GetFullPath((Split-Path -Parent $LabRoot))
    $datasetRoot = [IO.Path]::GetFullPath((Join-Path $LabRoot "datasets"))
    $insideRepo = $target.StartsWith(
        $repoRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase
    )
    if ($insideRepo) {
        $parent = [IO.Path]::GetFullPath((Split-Path -Parent $target))
        if (-not $parent.Equals($datasetRoot, [StringComparison]::OrdinalIgnoreCase) -or
            -not $target.EndsWith(".local.ndjson", [StringComparison]::OrdinalIgnoreCase)) {
            throw "Generated repository-local datasets must be direct homelab/datasets/*.local.ndjson files."
        }
    }
    $parentPath = Split-Path -Parent $target
    if (-not (Test-Path -LiteralPath $parentPath -PathType Container)) {
        throw "The dataset destination directory must already exist."
    }
    $datasetId = "synthetic-$ScaleProfile-$Records"
    $agentId = "integration-lab-$ScaleProfile"
    & $python (Join-Path $LabRoot "workload\dataset.py") generate $target `
        --records $Records --dataset-id $datasetId --agent-id $agentId --lab-root $LabRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Synthetic dataset generation failed."
    }
}

function Show-IntegrationCatalog {
    $python = Get-LabPython
    & $python (Join-Path $LabRoot "workload\catalog.py") $IntegrationCatalog
    if ($LASTEXITCODE -ne 0) {
        throw "Integration catalog could not be displayed."
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

if ($Command -eq "capacity-report") {
    $latestCapacity = Join-Path $Artifacts "latest-capacity-receipt.json"
    if (-not (Test-Path -LiteralPath $latestCapacity)) {
        throw "No capacity receipt exists yet. Run '.\lab.ps1 ingest-dataset' first."
    }
    Get-Content -Raw -LiteralPath $latestCapacity
    return
}

if ($Command -eq "list-integrations") {
    Show-IntegrationCatalog
    return
}

if ($Command -in @("check-dataset", "generate-dataset", "ingest-dataset")) {
    Set-LabScaleProfile
}

if ($Command -eq "generate-dataset") {
    New-LabDataset
    return
}

if ($Command -in @("check-dataset", "ingest-dataset")) {
    Set-LabDataset
    Test-LabDataset
}

if ($Command -eq "check-dataset") {
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
    "ingest-dataset" {
        Test-LabSample
        Test-LabDataset
        Invoke-Compose -RealModel -Arguments @("down", "--remove-orphans")
        Invoke-Compose -Arguments @("up", "--build", "-d")
        Invoke-Compose -Arguments @("--profile", "bulk", "build", "bulk-ingest")
        Invoke-Compose -Arguments @("--profile", "bulk", "run", "--rm", "--no-deps", "bulk-ingest")
        Show-Endpoints
        Write-Host "Capacity receipt: $Artifacts\latest-capacity-receipt.json"
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
    foreach ($name in $DatasetEnvironmentNames) {
        $previous = $PreviousDatasetEnvironment[$name]
        if ($previous.Present) {
            [Environment]::SetEnvironmentVariable($name, $previous.Value, "Process")
        }
        else {
            Remove-Item "Env:$name" -ErrorAction SilentlyContinue
        }
    }
}
