# Lians product direction

## Decision

Do not build generic Deploy All or Lians Ship as the company.

Deploy All is already replaceable by general coding agents and focused
builders. Ship identifies a real production problem, but it is limited to AI
builders, is likely to become custom engineering work, and has no direct Lians
demand evidence.

The strongest candidate from the new pressure test is an experiment, not a
proven pivot:

> **Refund.**

> Forward the receipt or support thread. Lians gets your money back.

Read the full [10 out of 10 pressure test](ten-out-of-ten-pressure-test.md) and
the binding [product parameters](product-parameters.md) before changing the
product.

## Why this candidate is stronger

Refund has a broad consumer trigger and a narrow first case type. It produces
money returned, not advice, code, or another draft. It also fits the existing
Lians forwarding and approval thesis better than an unrelated app builder.

ChatGPT or Claude can write a refund request. Lians only adds value if it keeps
the evidence and authority for the case, sends the approved request, reads the
reply, follows up, and verifies that the refund landed.

## Exact first scope

The free validation cohort accepts only U.S. merchant cases with:

- a duplicate charge;
- a canceled order that was still charged;
- a missing order covered by the merchant's stated policy;
- a returned purchase whose promised refund is late; or
- an approved refund that never posted.

The case must be addressable by email, worth at least $25, supported by a real
receipt or thread, and require no physical return or bank dispute.

## User experience

The user:

1. forwards the receipt or support thread;
2. answers only missing factual questions;
3. reviews the evidence, requested amount, and first action;
4. approves the message; and
5. leaves while Lians follows the case.

Visible states stay plain:

```text
UNSUPPORTED
NEEDS YOU
READY TO SEND
RECOVERING
REFUNDED
DENIED
```

`REFUNDED` requires user-confirmed evidence that the money landed. A generated
email or merchant promise does not count.

## What Lians would have to own

```text
FORWARD
  -> BUILD EVIDENCE FILE
  -> CHECK CASE BOUNDARY
  -> PROPOSE ACTION
  -> APPROVE
  -> SEND
  -> READ REPLY
  -> FOLLOW UP
  -> VERIFY MONEY RETURNED
```

The first durable product assets would be:

- a structured case record;
- merchant-specific policy and contact knowledge;
- evidence requirements by failure type;
- deterministic deadlines and safe escalation rules;
- an approval ledger;
- thread and outcome tracking; and
- measured resolution patterns.

## Why this is not proven

The category has strong demand evidence and direct competition. A current UK
competitor already markets nearly the same outcome and success-fee model.
Merchant systems vary, claims can be abused, low-dollar cases may not cover
operating cost, and Lians has not recovered one dollar for one user.

That is why the next step is a free manual cohort, not a platform build or a
website promise.

## First proof gate

Run twenty real cases manually behind a minimal forwarding and approval flow.
Continue only if:

- at least eight confirmed refunds land within fourteen days;
- confirmed refunds exceed $1,000 in total;
- user time remains below three minutes at the median;
- human operating time falls below fifteen minutes for the final five cases;
- no false or unauthorized claim is sent;
- four qualified referrals occur; and
- four people bring a second valid case within thirty days.

If ordinary ChatGPT-written emails resolve the same cases with the same effort,
if merchant exceptions keep human work high, or if users will not connect real
threads, stop.

## Current implementation boundary

Refund is not implemented. The shipped repository remains Lians Check, the
evidence-backed proof layer for AI coding work. No public copy may claim that
Lians currently recovers refunds.

## Explicit non-goals

- generic prompt-to-app generation;
- Lians Ship platform work;
- a chatbot that only drafts complaints;
- ordinary physical returns;
- chargebacks, legal threats, or regulated claims;
- autonomous messages without approval;
- every merchant or country;
- a guaranteed recovery claim;
- pricing before direct demand; and
- calling the idea proven before users receive money.
