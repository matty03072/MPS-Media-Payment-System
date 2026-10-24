# MPS licensing implementation

Status: owner licence service deployed; 1.0.0 client release includes application licensing.

## Requested outcome

- Allow operators to collect membership payments and use MPS commercially.
- Prohibit resale of MPS, including modified/rebranded copies.
- Free allowance of ten members; a purchased licence unlocks larger installations.
- Server-side SQL licence/order records, Stripe checkout, automatic email delivery,
  and a protected licence administration panel.
- Reuse the owner's existing Stripe account; do not ship its credentials to users.

## Confirmed commercial details

- GitHub-distributed MPS only; never apply the limit to the existing Members site.
- GBP 5 per month through Stripe. No one-time unlock.
- Check in with the licence server; failed/stopped payment returns the installation
  to ten-member mode. Keep all account information.

## Service details

- Purchase site: https://members.streaminghome.app/mps.
- Excess members lose MPS access only; linked Emby/Ombi accounts are not changed by licensing.

## Additional confirmed requirements

- Purchase and management pages are on the existing Members site.
- Licence customers have separate email/password accounts with mandatory MFA.
- Three active application registrations per subscription; fourth is refused.
- Record application UUID, first/latest IP and registration/change/removal history.
- Customer removal frees a slot and disables the old app on its next check (bounded
  by signed-check expiry offline). Preserve its data and licence-recovery access.

## Implementation boundaries

- New work lives on feature/licensing, outside the live membership application.
- Private signing keys stay on the issuing server; clients verify Ed25519 signatures.
- Codes bind to a persistent installation UUID. Backups must preserve that UUID.
- No licence is issued from a checkout success redirect. Fulfilment must re-fetch
  Stripe payment state, validate the expected price/currency/order/environment,
  and commit exactly one entitlement per order.
- Verified webhooks and asynchronous payment events drive durable fulfilment.
- Email is a retryable outbox operation after payment/entitlement commit. SMTP
  acceptance is not a guarantee of delivery; retries reuse the same code.
- Store licence orders separately from member subscription receipts and revenue.
- Production clients reject test licences. Existing members must not be deleted.
- Administrator access remains available for licence recovery and account management.
- Member admission must be serialised across invitation signup and imported-member
  creation, not merely hidden in the UI. The exact counted-member policy is to be
  displayed to administrators before release.
- Source-visible enforcement is not tamper-proof. The licence supplies legal terms;
  cryptographic signatures prevent forging legitimate unlock codes, not code edits.
- Previously published MIT grants cannot be assumed revoked by changing LICENSE,
  regardless of reported download counts. Preserve third-party notices.

## Verification required before release

Forged/cross-installation/test/expired tokens; 10/11 member boundary and concurrent
admission; administrative bypass paths; webhook signatures and duplicate/out-of-order
events; unpaid/wrong-price/wrong-mode checkout rejection; retry after payment and
SMTP failures; admin authentication/CSRF; SQL Server migration and restart; real
Stripe test checkout, email and activation. The cancellation/refund policy is now specified in the customer documentation: cancellation
at period end, full latest-payment refund for requests within 14 days, and paid access
retained until period end. Public seller/contact and policy disclosures are published on the purchase site.
