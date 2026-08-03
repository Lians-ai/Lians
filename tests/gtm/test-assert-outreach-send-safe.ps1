$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$guard = Join-Path $root "scripts\gtm\assert-outreach-send-safe.ps1"
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    "lians-outreach-guard-" + [guid]::NewGuid().ToString("N")
)
New-Item -ItemType Directory -Path $tempRoot | Out-Null
$log = Join-Path $tempRoot "outreach.csv"

try {
    @'
sent_at_et,account,recipient,subject,gmail_message_id,gmail_thread_id,status,next_follow_up,notes
2026-07-29,Existing Co,sales@existing.example,Paid design partnership,m1,t1,sent,2026-08-03,test
2026-07-29,Bounced Co,sales@bounced.example,Paid design partnership,m2,t2,bounced,,test
2026-07-29,Other Co,sales@other.example,Paid design partnership,m3,t3,sent,2026-08-03,test
'@ | Set-Content -LiteralPath $log -Encoding utf8

    $safe = & $guard `
        -Account "New Co" `
        -Recipient "sales@new.example" `
        -AsOf ([datetime]"2026-07-29") `
        -MaxDailyOutbound 5 `
        -LogPath $log
    if (-not $safe.safe_to_send_from_log) {
        throw "Expected a new account below the cap to pass."
    }

    $duplicateBlocked = $false
    try {
        & $guard `
            -Account "Existing Co" `
            -Recipient "new-route@existing.example" `
            -AsOf ([datetime]"2026-07-29") `
            -MaxDailyOutbound 5 `
            -LogPath $log | Out-Null
    }
    catch {
        $duplicateBlocked = $_.Exception.Message -match "already has an explicit paid proposal"
    }
    if (-not $duplicateBlocked) {
        throw "Expected duplicate account to be blocked."
    }

    $bounceBlocked = $false
    try {
        & $guard `
            -Account "Bounced Co" `
            -Recipient "sales@bounced.example" `
            -Mode HumanReply `
            -AsOf ([datetime]"2026-07-29") `
            -LogPath $log | Out-Null
    }
    catch {
        $bounceBlocked = $_.Exception.Message -match "suppressed"
    }
    if (-not $bounceBlocked) {
        throw "Expected bounced recipient to remain blocked."
    }

    $capBlocked = $false
    try {
        & $guard `
            -Account "Another New Co" `
            -Recipient "sales@another.example" `
            -AsOf ([datetime]"2026-07-29") `
            -MaxDailyOutbound 2 `
            -LogPath $log | Out-Null
    }
    catch {
        $capBlocked = $_.Exception.Message -match "conservative log count"
    }
    if (-not $capBlocked) {
        throw "Expected daily cap to be blocked."
    }

    Write-Output "Outreach send guard tests passed."
}
finally {
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
}
