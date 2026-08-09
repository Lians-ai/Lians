# Lians kickoff-payment authorization handoff

**Purpose:** Complete this internally before sending any buyer a payment link,
invoice, ACH instruction, or wire instruction. Do not paste bank account,
routing, card, API-key, or login credentials into this file.

## Officer authorization

- Authorizing officer name: `[required]`
- Officer title: `[required]`
- Authorization date: `[required]`
- Lians legal business address: `[required]`
- Authorized payment provider: `[required]`
- Lians-owned provider account identifier, last four characters only:
  `[required]`
- Buyer-facing statement descriptor: `[required]`
- Collection method: `[Stripe invoice / ACH / wire]`
- Officer confirms this account may receive revenue for **Lians AI**:
  `[yes / no]`
- Officer authorizes issuance of the first **$2,250 USD** kickoff invoice:
  `[yes / no]`

## Security rule

The full account number, routing number, API key, login credential, and secure
payment URL must stay in the authorized payment provider or an approved secret
channel. They must not be committed to the repository or sent to an unverified
recipient.

## Buyer details

Complete only after a buyer accepts the paid scope:

- Buyer legal entity: `[required]`
- Authorized signer: `[required]`
- Verified billing contact name: `[required]`
- Verified billing email: `[required]`
- Purchase-order required: `[yes / no]`
- Purchase-order number: `[required if applicable]`
- Signed order-form location: `[required]`
- Target kickoff date: `[required]`

## Invoice controls

- Invoice issuer exactly matches `Lians AI`: `[pass / fail]`
- Invoice amount is `$2,250.00 USD`: `[pass / fail]`
- Invoice references the signed AI Evidence Readiness Sprint: `[pass / fail]`
- Payment instructions originate from the authorized provider: `[pass / fail]`
- Recipient matches the verified billing contact: `[pass / fail]`
- Buyer-facing descriptor is correct: `[pass / fail]`

Do not issue the invoice unless every applicable control passes.

## Cleared-payment evidence

An invoice, payment link, promise to pay, pending ACH, or signed order form is
not cleared payment.

- Provider invoice ID: `[required]`
- Provider payment or charge ID: `[required]`
- Amount received: `[required]`
- Currency: `[required]`
- Provider status: `[must be paid/succeeded]`
- Cleared or settled timestamp: `[required]`
- Evidence reviewed by: `[required]`
- Secure evidence location: `[required; do not store sensitive banking data]`

## Release gate

Implementation may begin only when all four statements are true:

- `[ ]` The customer-specific order form is signed.
- `[ ]` The invoice came from an authorized Lians-owned payment account.
- `[ ]` The provider reports the $2,250 payment as paid or succeeded.
- `[ ]` The payment evidence has been independently reviewed and recorded.

If any box is unchecked, kickoff remains unscheduled.
