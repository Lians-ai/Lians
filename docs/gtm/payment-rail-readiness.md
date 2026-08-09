# Paid design-partnership payment readiness

## Current status

Verified through August 4, 2026:

- Lians has a separate Clerk application named `lians`; it is distinct from the
  OnePile Clerk application.
- The Lians live Clerk keys resolve to a **production** instance with the
  `lians.ai` primary domain and `clerk.lians.ai` frontend API domain.
- Clerk Billing returns five live user plans: Free, Starter, Growth, Pro, and
  Enterprise. All five configured plan identifiers match the live Clerk API.
- Starter is $15/month, Growth is $69/month, and Pro is $199/month.
- Clerk reports zero billing statements and the production Dashboard reports
  no subscriptions, so no Lians self-serve payment has been recorded.
- User billing is enabled. Clerk shows the production payment gateway as
  **Connected** to a Stripe account ending in `msj6`. This is distinct from the
  OnePile Stripe account ending in `V4o9`.
- Clerk requires a payment method for free trials in this production instance.
- Stripe marks account `msj6` **active** and reports no active account-status
  tasks. Its balance and processed volume are $0.
- A default USD bank account is connected. Payouts are configured as manual;
  no account-level payout statement descriptor is configured.
- The public Stripe profile uses `lians.ai` and the customer-facing statement
  descriptor `LIANS.AI`.
- Stripe's tax information is verified, but the business type is currently
  **Individual**. The legal name and tax identifier belong to the individual,
  not to Lians AI. No personal, bank, or tax values are recorded here.
- Stripe still requires phone verification before payments can be processed
  from the Dashboard. The account time zone was changed from UTC to
  `America/New_York` on August 4.
- The Stripe tax-details editor supports **Company** with several legal
  structures. It was previewed and then cancelled without changing the verified
  tax identity because the founder confirms no partnership or corporation has
  yet been legally formed and no state-filed entity record exists.
- The connected mailbox supports only a provisional working identity:
  `Lians AI, Corp.` and a claimed New York formation around July 21-22, 2026.
  It contains no state certificate, filing receipt, founder stock documents,
  cap table, or executed SAFE. Those self-reported facts are not a substitute
  for the legal records Stripe requires.
- An authoritative IRS CP 575 notice dated August 3 was found outside the
  repository, and the founder confirmed the IRS legal/tax name is **Lians AI**.
  The notice names a general partner and specifies Form 1065, which is an IRS
  partnership-classification signal; the founder confirms no partnership has
  yet been legally formed. The EIN itself and all personal/address details
  remain outside the repository. Investor and partner materials that say
  `Lians AI, Corp.` must be corrected. The business must first choose and form
  its legal entity, then reconcile the IRS record before Stripe is converted or
  any securities/ownership interests are issued.
- The live Lians upgrade page currently fails closed and routes paid tiers to
  sales. The cause is a public Enterprise plan priced at $1/month: the website
  treats Enterprise as custom/contact-sales and rejects that unexpected public
  plan before mounting Clerk's checkout.
- The Stripe CLI on this machine has only one profile. It is the **OnePile**
  account, ending in `V4o9`, with test and live access. Never use that profile
  for Lians revenue.

The account is technically active and connected, but it is **not a separate
Lians entity payment rail yet**. Until formation, processing through the
verified Individual profile would be revenue and liability of that individual,
not of a separate Lians entity. Before accepting revenue as a formed Lians AI
entity, the Stripe tax/business profile must be changed from Individual to
Company using reconciled state and IRS records; the payout bank must be
confirmed as an authorized entity account; phone verification must be
completed; and Stripe must show no new charge or payout restrictions after
reverification.

Clerk uses Stripe only as the processor; Lians remains the merchant. Clerk
plans and subscriptions are separate from Stripe Billing, so design-partner
invoices should be issued directly from the verified Lians Stripe account after
scope is signed.

## Immediate remediation

1. [x] Sign in to the Lians **production** instance in the Clerk Dashboard and
   open Billing settings.
2. [x] Record only the connected Stripe account's final four characters. Clerk
   shows account `msj6`, distinct from OnePile account `V4o9`.
3. [x] Sign in to Stripe and audit account `msj6`: active, no active tasks,
   default USD bank connected, manual payout schedule, verified Individual tax
   profile, and Dashboard phone verification still incomplete.
4. [ ] Use **Lians AI** as the confirmed IRS legal/tax name. Decide the intended
   legal structure with formation counsel/CPA, complete the state formation, and
   reconcile the resulting entity with the August 3 CP 575 notice's partnership
   filing indicators. Confirm whether the existing EIN record must be corrected
   or replaced. Then collect the authorized business address, representative
   details, and entity-bank evidence. Only after that work, update Stripe from
   **Individual** to **Company** with the matching legal structure. Stripe warns
   that this may create new verification requirements; complete them within the
   stated grace period and re-check both charges and payouts.
5. [ ] Confirm that the connected payout bank is authorized for Lians AI.
   Replace it if it is personal or belongs to another business.
6. [ ] Complete Stripe phone verification, add a payout statement descriptor,
   and choose an approved manual or automatic payout schedule. The account time
   zone is now `America/New_York`.
7. [ ] Confirm sales-tax registration and Stripe Tax requirements with the
   company's accountant before broad self-serve sales. Clerk does not determine
   or remit Lians' taxes.
8. [ ] Make Enterprise non-public in Clerk and remove or replace the $1 default
   price. Enterprise must remain a custom, sales-led offer unless an approved
   public price is adopted.
9. [ ] Re-open the live Lians upgrade page and verify that Starter, Growth, and Pro
   render and enter Clerk's live checkout without exposing Enterprise.
10. [ ] Before relying on self-serve revenue, run one authorized $15 live Starter
   transaction, verify the Lians descriptor and receipt, cancel it, and process
   the refund in Stripe. Record the evidence outside the repository. This is a
   real accounting event and should be done only by an authorized officer.
11. [ ] Configure direct Stripe invoice and payment-link branding for design-partner
   services; do not try to represent those contracts as Clerk subscriptions.

## Required before a buyer signs

1. Confirm the legal payment recipient and Stripe account.
2. Confirm the Lians legal business address for invoices.
3. Confirm the customer legal name, billing contact, and purchase-order
   requirement.
4. Create the kickoff invoice or payment link for the amount and schedule in the
   signed order form. The existing **$2,250 USD** amount applies only to a
   $4,500 offer with 50% due at kickoff; it must not be reused for a $20,000
   pilot without matching written terms.
5. Ensure the buyer-facing descriptor and receipt identify the correct legal
   entity.
6. Send the secure payment route only to the verified billing contact.
7. Record the invoice ID, payment confirmation ID, and settlement date.
8. Schedule kickoff only after the order form is signed and funds have cleared.

## Packet preparation

After commercial acceptance, use `scripts/prepare_paid_deal_packet.py` to
generate a customer-specific, still-interactive order form. The current packet
logic was built around the $4,500 readiness offer and must be reviewed before it
is used for the proposed $20,000 pilot. The script creates the kickoff invoice
only when the provider address, invoice number, invoice date, signed order-form
date, payment method, and secure payment instructions are all supplied together.
It verifies canonical AcroForm values, page widgets, and appearance streams
before returning either file.

Do not use synthetic test values or the visible OnePile payment account in a
buyer packet.

## Fast authorization handoff

An authorized Lians officer must provide or confirm all of the following in one
secure handoff:

- exact legal business address for `Lians AI`;
- authorized payment provider and Lians-owned account;
- buyer-facing statement descriptor;
- preferred collection method: ACH, wire, or Stripe invoice;
- officer approval to issue the first invoice for the exact signed offer.

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
- issued kickoff invoice matching the signed order form;
- authoritative payment confirmation;
- named workflow, technical owner, and kickoff date.

## Platform references

- [Clerk Billing overview and Stripe account requirements](https://clerk.com/docs/guides/billing/overview)
- [Clerk Billing plan visibility behavior](https://clerk.com/docs/reference/backend/types/billing-plan)
- [Clerk production-instance deployment requirements](https://clerk.com/docs/guides/development/deployment/production)
- [Stripe requirements for a US account](https://support.stripe.com/questions/requirements-for-having-a-us-stripe-account)
- [Stripe US beneficial-owner requirements](https://support.stripe.com/questions/beneficial-ownership-requirements-united-states)
- [Stripe verified-information update behavior](https://docs.stripe.com/connect/update-verified-information)
- [Stripe tax-name and EIN matching](https://support.stripe.com/questions/verify-your-tax-information-error-messages)
