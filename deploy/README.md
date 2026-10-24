# Owner-site deployment handoff

This extension must be staged and verified against your existing host before deployment.
Never put production secrets or the private signing key in this repository.

## Package boundary

Only deploy `licence_store/`, `mps_licence_tokens.py`, and the reviewed owner entry
point wrapper. Do NOT deploy the GitHub client's `mps/`, `LICENSE`, member limits,
or worker changes into StreamingHome. The wrapper calls the original
`streaminghome.create_app`, then adds the licence shop and customer-account routes.

Owner routes:
- `/mps`: £5/month purchase page.
- `/mps/register`, `/mps/login`, `/mps/account`: separate verified-email/MFA accounts.
- `/admin/mps-licences`: existing Members administrator authentication + MFA.
- `/mps/stripe/webhook`: separate Stripe signing secret and event inbox.
- `/mps/api/check-in`: activation-key-authenticated, installation-bound signed leases.

## Deployment sequence when access returns

1. Inspect live source/config/service definitions; reconcile drift with the local
   StreamingHome source. Preserve current server identity and existing SSH keys.
2. Take and verify SQL COPY_ONLY/CHECKSUM backup plus affected source backups.
3. Stage the extension against a disposable SQL Server database. Run customer,
   payment, concurrency and migration tests; confirm the live DB is never a test target.
4. Add the reviewed wrapper to the existing web entry point and an admin navigation
   link. No GitHub-client restriction code is imported by this wrapper.
5. Run `flask --app app:create_app mps-store-init` with the existing Members config.
   This creates only new `mps_licence_*` tables and a server-side private signing key.
   Restrict that key to the service identity and administrators. Keep it in backups.
6. Configure a separate worker service using the existing venv and configuration:
   `flask --app app:create_app mps-store-worker`. Follow the live host's existing
   service manager; do not run two licence workers intentionally. Stop/retry proof
   must show that email and event processing resume without new codes or charges.
7. Through the existing encrypted Stripe configuration, create/reuse a distinct MPS
   GBP 500 monthly price (quantity one, no trial/coupons) and a separate webhook.
   Never change the existing membership prices or membership webhook endpoint.
   Required events: checkout.session.completed, checkout.session.async_payment_succeeded,
   invoice.paid, invoice.payment_failed, customer.subscription.updated/deleted.
8. Configure the licence endpoint secret and price in `/admin/mps-licences`.
   Purchases default to disabled. Prove a real Stripe test checkout → signed webhook
   → licence issuance → email acceptance → activation before enabling live purchases.
9. Verify reverse-proxy handling before accepting registration IP evidence: only
   trust proxy-supplied addresses when the app is behind the controlled proxy.
10. Export ONLY the issuer public key into `mps/licensing_public.json` with
    `{"url":"https://your-members-site.example","public_key":"..."}`.
    Run the release preflight, full client/server tests and container smoke tests.
11. Publish a new GitHub/container version and update community/official package
    image references and licensing description. Never relabel existing MIT releases.
12. Verify public HTTPS, owner site health, customer MFA, £5 billing, three concurrent
    registrations, IP history, payment-failure fallback, removal disabling, and data
    retention. Report local/mocked checks separately from live Stripe/SQL/email proof.

## Current policy and open detail

- Up to three active application registrations per subscription. Fourth registration
  is refused; existing registrations stay active. Removal frees a slot and leaves
  a revoked record, so the removed ID cannot silently register again.
- Client checks every 15 minutes; signed leases last at most one hour. Network
  failure uses the last valid lease, then falls back to ten members. A received
  removal remains disabled even after its signed response expires.
- The free tier allows the ten oldest enabled non-admin accounts. All other accounts
  remain stored. Administrator access remains available for licence management.
- Removed application IDs disable normal app routes and background work, preserving
  data and licence recovery. This is not a tamper-proof DRM claim for visible source.
- Pending user clarification: on payment failure, should beyond-ten users lose only
  MPS access or also linked Emby/Ombi access? Current isolated implementation restricts
  MPS access only and does not revoke external media permissions.
- Custom no-resale terms are implemented locally, not a claim of legal certification.
