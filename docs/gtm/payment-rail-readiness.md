# Paid design-partnership payment readiness

## Current status

- Stripe CLI is installed and authenticated.
- The active Stripe account is `acct_1Ta3xbHUBWEOV4o9`.
- Charges and payouts are enabled.
- The account's dashboard display name and statement descriptor are **OnePile**.
- No Stripe key is configured in the Lians workspace environment.
- No Lians-specific payment link or kickoff invoice has been created.

Workspace audit on July 27, 2026 found no authoritative Lians legal business
address, tax identifier, bank instructions, or Lians-owned Stripe account.
Product billing code and blank Stripe configuration are implementation
artifacts, not authorization to collect design-partnership revenue.

Do not create or send a payment link from this account until an authorized
Lians representative confirms that the OnePile Stripe account may receive Lians
AI, Corp. revenue, or switches the CLI to the correct Lians Stripe account.

## Required before a buyer signs

1. Confirm the legal payment recipient and Stripe account.
2. Confirm the Lians legal business address for invoices.
3. Confirm the customer legal name, billing contact, and purchase-order
   requirement.
4. Create a one-time **$2,250 USD** kickoff invoice or payment link.
5. Ensure the buyer-facing descriptor and receipt identify the correct legal
   entity.
6. Send the secure payment route only to the verified billing contact.
7. Record the invoice ID, payment confirmation ID, and settlement date.
8. Schedule kickoff only after the order form is signed and funds have cleared.

## Packet preparation

After commercial acceptance, use `scripts/prepare_paid_deal_packet.py` to
generate a customer-specific, still-interactive order form. The script creates
the kickoff invoice only when the provider address, invoice number, invoice
date, signed order-form date, payment method, and secure payment instructions
are all supplied together. It verifies canonical AcroForm values, page widgets,
and appearance streams before returning either file.

Do not use synthetic test values or the visible OnePile payment account in a
buyer packet.

## Fast authorization handoff

An authorized Lians officer must provide or confirm all of the following in one
secure handoff:

- exact legal business address for `Lians AI, Corp.`;
- authorized payment provider and Lians-owned account;
- buyer-facing statement descriptor;
- preferred collection method: ACH, wire, or Stripe invoice;
- officer approval to issue the first $2,250 invoice.

Use `docs/gtm/lians-payment-authorization-handoff.md` as the internal control
sheet. Record only the payment-account identifier's final four characters in
the repository; keep credentials and full banking details in the authorized
provider or an approved secret channel.

Once supplied, generate the customer packet with:

```powershell
& 'C:\Users\jedie\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' `
  'scripts\prepare_paid_deal_packet.py' --help
```

Do not put bank account or routing numbers into the repository.

## Close evidence

The opportunity is not won until the repository or secure deal record contains:

- signed order form;
- issued $2,250 kickoff invoice;
- authoritative payment confirmation;
- named workflow, technical owner, and kickoff date.
