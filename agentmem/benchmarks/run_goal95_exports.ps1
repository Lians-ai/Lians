param(
    [int]$WaitForPid = 0,
    [int]$FromConversation = 2,
    [int]$ToConversation = 9
)

$ErrorActionPreference = 'Continue'
$env:PYTHONUTF8 = '1'
$env:AGENTMEM_ALLOW_UNENCRYPTED = 'true'
$env:MASTER_ENCRYPTION_KEY = ''
$env:SENTENCE_TRANSFORMER_MODEL = 'Snowflake/snowflake-arctic-embed-l-v2.0'

if ($WaitForPid -gt 0) {
    Wait-Process -Id $WaitForPid -ErrorAction SilentlyContinue
}

New-Item -ItemType Directory -Force -Path 'results\goal95' | Out-Null
$python = (Get-Command python).Source
for ($conversation = $FromConversation; $conversation -le $ToConversation; $conversation++) {
    $report = "results\goal95\conv_${conversation}_context_bundles.json"
    if (Test-Path -LiteralPath $report) {
        continue
    }
    $database = "results/goal95/conv_${conversation}.sqlite"
    $stdout = "results\goal95\conv_${conversation}.stdout.log"
    $stderr = "results\goal95\conv_${conversation}.stderr.log"
    & $python -m benchmarks.locomo_eval `
        --conv $conversation `
        --k 200 `
        --embeddings sentence-transformers `
        --db $database `
        --out $report `
        --dump-candidates `
        --include-context `
        1> $stdout 2> $stderr
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "LoCoMo export failed for conversation $conversation. See $stderr."
    }
}
