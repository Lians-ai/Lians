# Lians paid design-partnership pipeline

**As of:** July 27, 2026, 3:27 PM ET
**Win condition:** signed order form and cleared $2,250 kickoff payment
**Current wins:** 0
**Campaign target:** 150 unique delivered paid-proposal accounts

**Deliverability control:** July 27 activity exceeded the later-established
safe daily ceiling. New proposals and proactive follow-ups are paused through
July 28. The active automation is monitoring/response-only during the
cool-down. Starting July 29, the campaign-wide cap is five outbound messages
per day and every proactive send must pass
`scripts/gtm/assert-outreach-send-safe.ps1`, a full Gmail Sent count, and a
thread/suppression review.
**Qualified active/outreached accounts:** 112
**Human-response accounts:** 2
**Qualified buyer-interest accounts:** 0
**Negative-response accounts:** 2

Delivery audit: 14 failed routes are excluded. Uniphore, Qventus, and Sprout.ai were replaced by Salv, Abridge, and Theta Lake through published company routes. EliseAI's explicit opt-out was permanently suppressed and replaced by Atrial AI through its current sales inbox.

Conversion readiness: the July 28 Confluent brief now requires a named workflow, buyer, budget path, and warm introduction—not merely architecture validation. ValidMind's paid follow-up remains queued for the agreed 5:00 PM ET checkpoint, subject to a final no-reply and delivery check.

Strict campaign audit: **100 unique companies now have a delivered, explicit paid proposal** in the outreach log, with zero duplicate explicit-proposal accounts. General introductions, meetings, drafts, bounced mail, opted-out accounts, and free-pilot language are excluded. The 3:08 PM ET audit passed, and no new delivery failure was present for Atrial AI.

Evidence hardening: every replacement send has a confirmed Gmail message ID. `scripts/gtm/audit-paid-outreach.ps1` fails if the total falls below 100 or any explicit-proposal account is duplicated; its 3:08 PM run passed while correctly reporting zero signed accounts and zero cleared kickoff payments.

Response classification: Vouch's direct no and EliseAI's opt-out are tracked as
human negative responses. SEON and ThetaRay acknowledgements are automated
receipts and do not count as buyer interest. No account has yet supplied a
named funded workflow, budget owner, scope request, signature, or kickoff
payment.

Close focus: `docs/gtm/paid-close-priority-queue-2026-07-27.md` ranks Marker, xysq, Protum, Confluent, and ValidMind as the shortest current paths to a commercial decision. Each next interaction must produce a named workflow, approver, budget path, success criterion, and dated action; technical interest alone does not advance the paid pipeline.

## Highest-leverage conversion events

| Date | Account | Event | Required outcome |
|---|---|---|---|
| Jul 28 | Confluent | Discovery meeting | Named technical counterpart plus one consequential-AI customer introduction; Confluent itself is not presumed to be the buyer |
| Jul 29 | xysq | Founder meeting | Named internal buyer and sanitized workflow, or one named customer with budget |
| Jul 29 | Protum | Founder meeting | Named internal budget owner, or one named regulated customer that can fund the sprint |
| Jul 29 | Marker | Paid follow-up due | Written yes/no on a funded Marker workflow or named customer with budget |

## Paid terms in market

| Account | Current evidence | Next action | Follow-up |
|---|---|---|---|
| Marker | Paid terms sent in warm founder thread | Ask for approver, workflow, and success criterion; send order form only after commercial acceptance | Jul 29 |
| Protum | Meeting booked; paid terms sent | Qualify internal budget or require a named customer introduction | Jul 29 |
| xysq | Meeting booked; paid terms sent | Qualify internal budget or require a named customer introduction | Jul 29 |
| Incode | Official partner form confirmed | Follow up if no response within promised two-business-day window | Jul 30 |
| Alloy | Official application confirmed | Follow up once, referencing the submitted paid sprint | Jul 31 |
| Unit21 | Official application routed to Partnerships | Follow up once, requesting the product or AI-platform budget owner | Jul 31 |
| Sardine | Published sales route; paid terms sent | Request direct routing or a funded-workflow no | Jul 31 |
| Socure | Published enterprise sales route; paid terms sent | Request RiskOS/product budget owner after the stale partner address bounced | Jul 31 |
| AstraSync | Published founder route; paid terms sent | Request named KYA workflow and budget decision | Jul 31 |
| Brine | Published sales route; paid terms sent | Request product/platform buyer and duplication decision | Jul 31 |
| Credo AI | Published product leader; paid terms and one-pager sent | Request funded Governance Assistant/platform workflow | Jul 31 |
| Holistic AI | Published co-founder route; paid terms and one-pager sent | Request funded Agent Graph/Guardian Agent workflow | Jul 31 |
| Amantra | Published business route; paid terms sent | Request funded Agentic KYC or reconciliation workflow | Jul 31 |
| Elucidate | Published business route; paid terms sent | Request funded KYB, KYC, or due-diligence workflow | Jul 31 |
| Hawk | Published company route; paid terms and one-pager sent | Request funded AML Investigative Agent workflow and product budget owner | Jul 31 |
| Flagright | Published company route; paid terms and one-pager sent | Request funded AI Forensics workflow and budget owner | Jul 31 |
| SymphonyAI Financial Services | Published commercial route; paid terms and one-pager sent | Request funded Sensa/AML workflow and product budget owner | Jul 31 |
| Tookitaki | Published company route; paid terms and one-pager sent | Request funded FinMate/FinCense workflow and product budget owner | Jul 31 |
| Napier AI | Published company route; paid terms and one-pager sent | Request funded pCRA/screening workflow and product budget owner | Jul 31 |
| NICE Actimize | Published direct route; paid terms and one-pager sent | Request funded autonomous-AML workflow and product budget owner | Jul 31 |
| Fenergo | Published sales route; paid terms and one-pager sent | Request funded AI-driven KYC workflow and product budget owner | Jul 31 |
| ThetaRay | Published company route; paid terms and one-pager sent | Request funded RAY investigation workflow and product budget owner | Jul 31 |
| Quantexa | Published company route; paid terms and one-pager sent | Request funded Agent Gateway/Decision Intelligence workflow and product budget owner | Jul 31 |
| BioCatch | Published company route; paid terms and one-pager sent | Request funded agentic-fraud workflow and product budget owner | Jul 31 |
| Persona | Published company route; paid terms and one-pager sent | Request funded Case Review Agent/KYA workflow and product budget owner | Jul 31 |
| ComplyCube | Published company route; paid terms and one-pager sent | Request funded identity/AML workflow and product budget owner | Jul 31 |
| Trulioo | Published company route; paid terms and one-pager sent | Request funded Agentic Identity workflow and product budget owner | Jul 31 |
| Forter | Published sales route; paid terms and one-pager sent | Request funded agentic-commerce workflow and product budget owner | Jul 31 |
| Sift | Published sales route; paid terms and one-pager sent | Request funded fraud-decision workflow and product budget owner | Jul 31 |
| Riskified | Published sales route; paid terms and one-pager sent | Request funded commerce-decision workflow and product budget owner | Jul 31 |
| Arthur AI | Published company route; paid terms and one-pager sent | Request funded governed-agent workflow and product budget owner | Jul 31 |
| Arize AI | Published company route; paid terms and one-pager sent | Request funded regulated-agent workflow and product budget owner | Jul 31 |
| Patronus AI | Published company route; paid terms and one-pager sent | Request funded agent-evaluation workflow and product budget owner | Jul 31 |
| Braintrust | Published company route; paid terms and one-pager sent | Request funded immutable-evaluation workflow and product budget owner | Jul 31 |
| ValidMind | Paid end-of-day follow-up drafted and scheduled | Verify no intervening reply, then send the $4,500 scope at 5:00 PM ET | Jul 27 |

## Ecosystem routes that do not count as wins

- **Grafana:** warm integration relationship; paused until the agreed follow-up
  date. A marketplace or partner outcome is not a paid design partnership.
- **Confluent:** architecture and customer-introduction route unless Confluent
  explicitly becomes the budget owner.
- **Vouch:** disqualified. Vouch agreed the problem is real, but said its
  Partnerships team only handles referrals and the company is not reviewing
  new AI tooling for risk review or claims operations.
- **WorkFusion and Hummingbird:** technically high-fit regulated-AI accounts,
  but their official sites expose sales/demo forms rather than a verified
  buyer email. Keep qualified; do not invent contact data.

## Commercial assets ready

- `output/pdf/lians-paid-design-partnership.pdf`
- `output/pdf/lians-paid-design-partnership-order-form.pdf`
- `output/pdf/lians-kickoff-invoice-template.pdf`
- `docs/gtm/kickoff-invoice-template.md`
- `docs/gtm/paid-close-gates.md`

## Blocking operational issue

The only authenticated Stripe account currently visible is branded **OnePile**.
Do not issue a Lians payment link through it without explicit authorization or
switching to the correct Lians account. A signed order form is not a win until
the kickoff payment clears through an authorized rail.

## Active cadence

The thread heartbeat `send-validmind-paid-follow-up`, displayed as **Advance
Lians paid pipeline**, runs at 9:00 AM and 5:00 PM. It reads each due Gmail
thread before acting, respects recorded pause and follow-up dates, sends no
duplicate outreach, and preserves the paid terms. It must not use the OnePile
Stripe account without explicit Lians authorization. Because 114 log entries
currently share July 31 as a follow-up date, the heartbeat must rank warm,
human-routed, high-capacity accounts and send no more than 15 follow-ups per
calendar day; it must not blanket-send that queue.
