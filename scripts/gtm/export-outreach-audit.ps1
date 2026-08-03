param(
    [string]$LogPath = "docs/gtm/outreach-log-2026-07.csv",
    [string]$OutputPath = "docs/gtm/outreach-recipient-audit-2026-07-27.md"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $LogPath)) {
    throw "Outreach log not found: $LogPath"
}

$rows = @(Import-Csv -LiteralPath $LogPath)
$explicitStatuses = @(
    "paid-terms-sent",
    "paid-design-partnership-proposed",
    "paid-fit-meeting-booked"
)
$explicitRows = @(
    $rows | Where-Object {
        (
            $_.status -in $explicitStatuses -or
            ($_.status -eq "sent" -and $_.subject -like "Paid design partnership:*")
        ) -and
        -not [string]::IsNullOrWhiteSpace($_.gmail_message_id)
    }
)
$delivered = @(
    $explicitRows |
        Sort-Object account, sent_at_et |
        Group-Object account |
        ForEach-Object { $_.Group | Select-Object -Last 1 } |
        Sort-Object account
)
$bounces = @($rows | Where-Object status -eq "bounced" | Sort-Object account)
$suppressed = @(
    $rows |
        Where-Object status -in @(
            "opted-out",
            "disqualified",
            "rejected",
            "introduction-unreachable"
        ) |
        Sort-Object account
)

$duplicateCount = @(
    $explicitRows | Group-Object account | Where-Object Count -gt 1
).Count

$lines = [System.Collections.Generic.List[string]]::new()
$lines.Add("# Lians outreach recipient audit - July 27, 2026")
$lines.Add("")
$lines.Add("Generated from ``$LogPath``. Gmail reconciliation is recorded below.")
$lines.Add("")
$lines.Add("## Summary")
$lines.Add("")
$lines.Add("- Delivered explicit paid-proposal accounts: **$($delivered.Count)**")
$lines.Add("- Duplicate explicit paid-proposal accounts: **$duplicateCount**")
$lines.Add("- Recorded failed routes: **$($bounces.Count)**")
$lines.Add("- Qualified buyer-interest accounts: **0**")
$lines.Add("- Signed accounts: **0**")
$lines.Add("- Cleared kickoff payments: **0**")
$lines.Add("")
$lines.Add("A delivered message is not evidence that a human read it. A meeting,")
$lines.Add("automated acknowledgment, referral, or warm introduction is not buyer")
$lines.Add("interest without a named workflow, owner, budget path, paid acceptance,")
$lines.Add("and dated action.")
$lines.Add("")
$lines.Add("## Response reconciliation")
$lines.Add("")
$lines.Add("- **Negative human responses:** EliseAI opted out; Vouch declined the")
$lines.Add("  tooling/design-partner path.")
$lines.Add("- **Automated acknowledgments:** SEON and ThetaRay confirmed receipt only.")
$lines.Add("- **Warm/logistics responses:** Marker meeting logistics and Boardy")
$lines.Add("  introduction activity do not constitute purchasing interest.")
$lines.Add("- **Qualified positive buyer responses:** none verified.")
$lines.Add("")
$lines.Add("## Delivered explicit paid proposals")
$lines.Add("")
$lines.Add("| Account | Route | Sent | Follow-up date |")
$lines.Add("|---|---|---:|---:|")
foreach ($row in $delivered) {
    $account = $row.account -replace "\|", "\|"
    $recipient = $row.recipient -replace "\|", "\|"
    $followUp = if ($row.next_follow_up) { $row.next_follow_up } else { "-" }
    $lines.Add("| $account | $recipient | $($row.sent_at_et) | $followUp |")
}

$lines.Add("")
$lines.Add("## Failed routes - permanently suppress exact route")
$lines.Add("")
$lines.Add("| Account | Failed route | Reason |")
$lines.Add("|---|---|---|")
foreach ($row in $bounces) {
    $account = $row.account -replace "\|", "\|"
    $recipient = $row.recipient -replace "\|", "\|"
    $reason = ($row.notes -replace "\|", "\|" -replace "\r?\n", " ").Trim()
    $lines.Add("| $account | $recipient | $reason |")
}

$lines.Add("")
$lines.Add("## Other suppressions and closed paths")
$lines.Add("")
$lines.Add("| Account | Route | Status | Reason |")
$lines.Add("|---|---|---|---|")
foreach ($row in $suppressed) {
    $account = $row.account -replace "\|", "\|"
    $recipient = $row.recipient -replace "\|", "\|"
    $reason = ($row.notes -replace "\|", "\|" -replace "\r?\n", " ").Trim()
    $lines.Add("| $account | $recipient | $($row.status) | $reason |")
}

$lines.Add("")
$lines.Add("## Monitoring and send controls")
$lines.Add("")
$lines.Add("- No new proposals or proactive follow-ups through July 28.")
$lines.Add("- Human replies are read in full and classified before any response.")
$lines.Add("- Automated acknowledgments never count as interest.")
$lines.Add("- Starting July 29, proactive sends are capped at five total per day.")
$lines.Add("- Every proactive send must pass")
$lines.Add("  ``scripts/gtm/assert-outreach-send-safe.ps1`` and a separate Gmail Sent")
$lines.Add("  and thread review.")
$lines.Add("- Grafana remains paused until its recorded follow-up date.")
$lines.Add("")
$lines.Add("## Commercial completion gate")
$lines.Add("")
$lines.Add("The campaign is not won until one buyer has a signed customer-specific")
$lines.Add("scope and authoritative evidence that the $2,250 kickoff payment cleared.")

$output = [System.IO.Path]::GetFullPath(
    (Join-Path (Get-Location) $OutputPath)
)
$parent = Split-Path -Parent $output
if (-not (Test-Path -LiteralPath $parent)) {
    New-Item -ItemType Directory -Path $parent | Out-Null
}
[System.IO.File]::WriteAllLines($output, $lines)
Write-Output $output
