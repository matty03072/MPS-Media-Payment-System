# Changelog

## 1.0.0 — application licensing

- Added ten-member free allowance and £5/month subscription unlock for up to three applications.
- Added installation-bound signed leases, periodic checks, retained-data fallback and registration removal.
- Added separate verified-email/MFA licence accounts and owner-only Stripe fulfilment/email service.
- Added MPS-branded licence storefront and email templates.
- Changed new-build terms to the MPS Source-Available No-Resale Licence; membership-service payments remain permitted and earlier grants remain unchanged.
- Fixed Docker context inclusion of the shared licence verifier and added candidate image checks.
- Added authenticated private support with anti-spam checks and retryable delivery.
- Added self-service period-end cancellation and idempotent full refunds requested within 14 days of payment, including renewals.
- Added seller, cancellation/refund and privacy pages plus durable subscription terms in licence emails.
- Verified a real sandbox webhook, automatic licence email receipt, activation and cancellation/refund.
- Published seller/contact and cancellation/privacy notices; support remains a signed-in form.

## 0.1.0-beta.5

- Added Configuration → Branding with a custom site name and validated logo uploads.
- Applied branding to public/member/admin pages and new emails, including sender display name, plain-text and HTML content, and embedded logos.
- Kept customer branding and linked MPS project attribution in both footers.
- Branding is stored in the existing database and survives container updates.
- Corrected invitation setup guidance to point to Configuration → Emby servers.


## 0.1.0-beta.4

- Integrated the deployed application changes verified on 23 October 2026.
- Added Sonarr/Radarr connected-service setup and member-scoped request progress.
- Added optional deduplicated Emby scans after confirmed imports.
- Split configuration into category pages with desktop/mobile navigation.
- Added Emby server-detail editing and catalogue rescanning with linked-account checks.
- Kept media search above request tracking and updated dashboard progress.
- Preserved MPS branding, first-run owner setup, SQLite migration/WAL settings,
  storage paths, app ID and container security settings. No database schema change.

Production source was read-only. No production data, credentials, config files,
branding images or service state were copied. Configure the preview's integrations
separately. Live external-service behaviour remains dependent on your configuration.
