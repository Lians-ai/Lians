param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Account,

    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[^@\s]+@[^@\s]+\.[^@\s]+$")]
    [string]$Recipient,

    [ValidateSet("NewProposal", "FollowUp", "HumanReply")]
    [string]$Mode = "NewProposal",

    [ValidateRange(1, 100)]
    [int]$MaxDailyOutbound = 5,

    [datetime]$AsOf = (Get-Date),

    [string]$LogPath = "docs/gtm/outreach-log-2026-07.csv"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $LogPath)) {
    throw "Outreach log not found: $LogPath"
}

$rows = @(Import-Csv -LiteralPath $LogPath)
$accountKey = $Account.Trim().ToLowerInvariant()
$recipientKey = $Recipient.Trim().ToLowerInvariant()
$dateKey = $AsOf.ToString("yyyy-MM-dd")

$accountRows = @(
    $rows | Where-Object {
        $_.account -and $_.account.Trim().ToLowerInvariant() -eq $accountKey
    }
)
$recipientRows = @(
    $rows | Where-Object {
        $_.recipient -and $_.recipient.Trim().ToLowerInvariant() -eq $recipientKey
    }
)

$suppressedStatuses = @(
    "bounced",
    "opted-out",
    "disqualified",
    "rejected",
    "closed",
    "introduction-unreachable"
)
$suppressed = @(
    @($accountRows) + @($recipientRows) |
        Where-Object { $_.status -in $suppressedStatuses }
)
if ($suppressed.Count -gt 0) {
    $reasons = $suppressed |
        ForEach-Object { "$($_.account):$($_.status)" } |
        Sort-Object -Unique
    throw "SEND BLOCKED: account or recipient is suppressed ($($reasons -join ', '))."
}

$explicitPaidStatuses = @(
    "paid-terms-sent",
    "paid-design-partnership-proposed",
    "sent"
)
$priorExplicitPaid = @(
    $accountRows | Where-Object { $_.status -in $explicitPaidStatuses }
)
if ($Mode -eq "NewProposal" -and $priorExplicitPaid.Count -gt 0) {
    throw "SEND BLOCKED: $Account already has an explicit paid proposal in the log."
}

$followUpStatuses = @("warm-follow-up", "paid-follow-up-sent")
$priorFollowUps = @(
    $accountRows | Where-Object { $_.status -in $followUpStatuses }
)
if ($Mode -eq "FollowUp") {
    if ($priorExplicitPaid.Count -eq 0) {
        throw "SEND BLOCKED: no delivered explicit paid proposal is recorded for $Account."
    }
    if ($priorFollowUps.Count -ge 1) {
        throw "SEND BLOCKED: $Account already has the maximum one proactive follow-up."
    }
}

$nonSendStatuses = @(
    "paid-follow-up-drafted",
    "qualified-not-submitted",
    "paid-fit-meeting-booked",
    "meeting-booked"
)
$todayOutbound = @(
    $rows | Where-Object {
        $_.sent_at_et -eq $dateKey -and
        $_.gmail_message_id -and
        $_.status -notin $nonSendStatuses
    }
)

if ($Mode -ne "HumanReply" -and $todayOutbound.Count -ge $MaxDailyOutbound) {
    throw (
        "SEND BLOCKED: the conservative log count is " +
        "$($todayOutbound.Count)/$MaxDailyOutbound outbound messages for $dateKey. " +
        "Also verify Gmail Sent before sending."
    )
}

[pscustomobject]@{
    safe_to_send_from_log = $true
    account               = $Account.Trim()
    recipient             = $Recipient.Trim()
    mode                  = $Mode
    date                  = $dateKey
    conservative_log_count = $todayOutbound.Count
    max_daily_outbound    = $MaxDailyOutbound
    gmail_sent_check_required = $true
    thread_read_required  = $true
}
