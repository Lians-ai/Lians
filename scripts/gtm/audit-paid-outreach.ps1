param(
    [string]$LogPath = "docs/gtm/outreach-log-2026-07.csv",
    [int]$RequiredPaidProposals = 150
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $LogPath)) {
    throw "Outreach log not found: $LogPath"
}

$rows = @(Import-Csv -LiteralPath $LogPath)
$requiredColumns = @(
    "sent_at_et",
    "account",
    "recipient",
    "subject",
    "gmail_message_id",
    "gmail_thread_id",
    "status",
    "next_follow_up",
    "notes"
)

$actualColumns = @($rows[0].PSObject.Properties.Name)
$missingColumns = @($requiredColumns | Where-Object { $_ -notin $actualColumns })
if ($missingColumns.Count -gt 0) {
    throw "Missing required columns: $($missingColumns -join ', ')"
}

$explicitProposalStatuses = @(
    "paid-terms-sent",
    "paid-design-partnership-proposed",
    "paid-fit-meeting-booked"
)
$humanResponseStatuses = @(
    "replied",
    "substantive-reply",
    "named-buyer",
    "budget-confirmed",
    "scope-requested",
    "scope-sent",
    "order-form-sent",
    "signed",
    "kickoff-payment-cleared",
    "partnership-review",
    "disqualified",
    "opted-out",
    "rejected"
)
$qualifiedInterestStatuses = @(
    "named-buyer",
    "budget-confirmed",
    "scope-requested",
    "scope-sent",
    "order-form-sent",
    "signed",
    "kickoff-payment-cleared"
)
$negativeResponseStatuses = @(
    "disqualified",
    "opted-out",
    "rejected"
)
$suppressedStatuses = @(
    "bounced",
    "disqualified",
    "opted-out",
    "rejected"
)

$explicitRows = @(
    $rows | Where-Object {
        (
            $_.status -in $explicitProposalStatuses -or
            ($_.status -eq "sent" -and $_.subject -like "Paid design partnership:*")
        ) -and
        -not [string]::IsNullOrWhiteSpace($_.gmail_message_id)
    }
)
$explicitAccounts = @($explicitRows.account | Sort-Object -Unique)
$duplicateAccounts = @(
    $explicitRows |
        Group-Object account |
        Where-Object Count -gt 1 |
        Sort-Object Name
)
$bounceRows = @($rows | Where-Object status -eq "bounced")
$humanResponseAccounts = @(
    $rows |
        Where-Object status -in $humanResponseStatuses |
        Select-Object -ExpandProperty account -Unique
)
$qualifiedInterestAccounts = @(
    $rows |
        Where-Object status -in $qualifiedInterestStatuses |
        Select-Object -ExpandProperty account -Unique
)
$negativeResponseAccounts = @(
    $rows |
        Where-Object status -in $negativeResponseStatuses |
        Select-Object -ExpandProperty account -Unique
)
$signedAccounts = @(
    $rows |
        Where-Object status -eq "signed" |
        Select-Object -ExpandProperty account -Unique
)
$paidAccounts = @(
    $rows |
        Where-Object status -eq "kickoff-payment-cleared" |
        Select-Object -ExpandProperty account -Unique
)
$dueFollowUpAccounts = @(
    $rows |
    Where-Object {
        -not [string]::IsNullOrWhiteSpace($_.next_follow_up) -and
        [datetime]$_.next_follow_up -le (Get-Date).Date -and
        $_.status -notin $suppressedStatuses
    } |
    Select-Object -ExpandProperty account -Unique
)

$result = [ordered]@{
    required_explicit_paid_proposals = $RequiredPaidProposals
    delivered_explicit_paid_accounts = $explicitAccounts.Count
    duplicate_explicit_paid_accounts = $duplicateAccounts.Count
    recorded_bounces = $bounceRows.Count
    tracked_human_response_accounts = $humanResponseAccounts.Count
    qualified_buyer_interest_accounts = $qualifiedInterestAccounts.Count
    negative_response_accounts = $negativeResponseAccounts.Count
    signed_accounts = $signedAccounts.Count
    kickoff_payments_cleared = $paidAccounts.Count
    follow_up_accounts_due_or_overdue = $dueFollowUpAccounts.Count
    campaign_requirement_met = (
        $explicitAccounts.Count -ge $RequiredPaidProposals -and
        $duplicateAccounts.Count -eq 0
    )
    goal_complete = (
        $explicitAccounts.Count -ge $RequiredPaidProposals -and
        $duplicateAccounts.Count -eq 0 -and
        $signedAccounts.Count -gt 0 -and
        $paidAccounts.Count -gt 0
    )
}

[pscustomobject]$result | Format-List

if ($duplicateAccounts.Count -gt 0) {
    Write-Host "Duplicate explicit-proposal accounts:"
    $duplicateAccounts | ForEach-Object {
        Write-Host "  $($_.Name): $($_.Count)"
    }
}

if ($explicitAccounts.Count -lt $RequiredPaidProposals -or $duplicateAccounts.Count -gt 0) {
    exit 1
}
