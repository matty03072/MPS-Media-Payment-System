![MPS — Media Payment System](static/mps-logo.svg)

# MPS — Media Payment System

**One place to manage media memberships, payments and connected service accounts.**

MPS is a self-hosted application that brings **Emby, Emby Connect, Ombi, Sonarr, Radarr, Stripe, PayPal and email** together in one member portal and administrator dashboard. It connects the journey from invitation and signup to account creation, subscriptions and media access—reducing the need to manage every member separately in each service.

Members can manage their membership, open Emby, discover and request content through Ombi, and manage their account security. Administrators can invite members, choose their servers and libraries, configure plans, monitor account setup and track revenue from the same application.

## Screenshots

Captured from the running application with temporary MPS branding applied in the browser. Names and email addresses were removed before blurring; no production configuration or account data is bundled with the preview. These images illustrate the interface, not preconfigured integrations.

### Administrator overview

![MPS administrator overview](docs/screenshots/admin-overview.png)

### Member dashboard

![MPS member dashboard with identity obscured](docs/screenshots/member-dashboard.png)

### Member request statuses

![MPS member request statuses](docs/screenshots/member-request-status-redacted-v2.png)

## Automated account creation

Once the administrator has connected the services, an invited member signs up through MPS with their email, username and password. After email verification, the background worker coordinates account setup:

- **Emby servers:** creates the member's account on the servers included in their invitation, sets their password, applies the selected library and optional Live TV permissions, and enables access according to their membership entitlement.
- **Emby Connect:** attempts to authenticate an existing account using the signup details or register a new account, then links the member's Emby server accounts to that Connect identity. Emby's own email verification may still be required.
- **Ombi:** creates and links a regular member account when needed, using the signup details. An existing matching account is linked only after identity and sign-in checks. Setup waits until the member is entitled to access and Ombi is configured.

**Members do not need separate account-creation forms for each service.** MPS tracks progress, retries recoverable failures and flags conflicts for administrator review rather than blindly creating duplicate accounts or overwriting unrelated users. Emby Connect verification and account conflicts can still require member or administrator action.

## How the systems work together

| System | What MPS brings into the portal |
| --- | --- |
| **Emby** | Multi-server account creation, selected library and Live TV permissions, membership-based access control and linked-account management. Media playback remains in Emby. |
| **Emby Connect** | New-account registration or authenticated account linking, connecting the member's server accounts to their Connect identity. |
| **Ombi** | Automated member-account setup, authentication, content discovery and requests using the member's own linked account. |
| **Sonarr and Radarr** | Member-scoped download status, queue percentage/ETA and TV episode import counts, with optional Emby library scans after confirmed imports. |
| **Stripe and PayPal** | Optional subscription checkout, payment-status reconciliation and cancellation workflows connected to membership entitlement. |
| **SMTP email** | Branded invitations, verification, password recovery, account notifications and optional media-availability messages. |
| **Background worker** | Account provisioning, access reconciliation, retries and queued account-maintenance tasks. |

MPS coordinates these services through their APIs; it does not replace the media server, request engine or payment provider. Administrators supply their own service URLs, credentials and settings before automation can run.

## Connected services and request progress

Configure **Sonarr** and **Radarr** under **Configuration → Connected services**. Test a connection without saving, save encrypted API keys, choose optional Emby scan targets, or disable tracking. No connections or credentials from another deployment are bundled.

Members see only their own request progress on the dashboard and Requests page. Background checks run approximately every minute; refresh the page to see the latest status. Download percentages and estimated completion come from active queues. TV import progress counts requested episodes with imported files; film imports show status rather than an invented file-copy percentage.

Optional Emby scans are deduplicated and triggered only after import evidence, for selected servers accessible to that member. “Available to watch” requires confirmation in their accessible Emby libraries. Sonarr/Radarr access is read-only; enabling this feature does not grant new library permissions.

Configuration is organised into separate Emby, connected-service, SMTP, payment, Ombi and system pages. Emby server details can be edited and libraries rescanned without automatically changing existing member access.

## Your branding

Open **Configuration → Branding** to set your site name and upload a PNG, JPEG or WebP logo (up to 500 KB). Changes apply immediately to the site and newly sent emails, including the sender display name. Branding persists with your installation data. Site and email footers show your identity alongside a linked **MPS — Media Payment System** credit. The SMTP sending address remains the address configured under SMTP settings.

![Custom branding configuration](docs/screenshots/branding-settings.png)

## From invitation to access

1. **Invite:** an administrator selects the member's Emby servers, libraries, optional Live TV access and membership plan.
2. **Join:** the member accepts the invitation, creates an MPS account and verifies their email.
3. **Provision:** MPS coordinates Emby, Emby Connect and Ombi account creation and linking in the background.
4. **Activate:** free or paid membership entitlement determines access; optional payment integrations feed subscription status back into MPS.
5. **Manage:** the member uses one portal for membership, requests and account management, while administrators monitor setup, payments and access.

## Ongoing member management

- Central administration for invitations, members, plans, connected servers and administrator revenue attribution.
- Password changes and recovery resets can be propagated to verified linked Emby server and Ombi accounts, with retries when a service is unavailable.
- Membership changes feed into Emby access reconciliation.
- Account-removal workflows coordinate linked-service cleanup and subscription cancellation while retaining historical revenue attribution.
- Mandatory administrator MFA and optional member MFA.

**Emby Connect remains a separate global account.** Later MPS password changes do not change its password, and deleting an MPS account does not delete the global Emby Connect account. Existing remote accounts are subject to ownership and conflict checks—not automatically overwritten.

## Install the umbrelOS preview

This is a **community test preview**, published by matty03072—not an official Umbrel App Store release.

Add the [MPS community store](https://github.com/matty03072/MPS-Media-Payment-System-Store) to umbrelOS, then install **MPS — Media Payment System**. Existing preview users should update the installed app rather than uninstall it.

On first launch, unlock `/setup` with username `owner` and the password shown by Umbrel, create your permanent owner account and enrol MFA.

### Preview scope

Use test accounts and integrations. The default Umbrel proxy protects every route, so public invitations and external payment callbacks require a separately configured and verified public HTTPS deployment; they are **not enabled by the default preview package**. Automated account creation also requires configured services, a running worker and any required membership entitlement.

Container tests do not prove every third-party service works in your environment. Complete an end-to-end test with your own services before relying on the automation. No production database, service credentials or member records are included.

## Development

Install `requirements.txt`, then run:

```sh
python -m unittest installer.test_installation installer.test_umbrel.UmbrelTests installer.test_branding.BrandingTests -q
PYTHONPATH=.:tests python -m unittest discover -s tests -p 'test_*.py' -q
```

Images target `linux/amd64` and `linux/arm64`. Configuration, database and encryption keys persist together in `/state`. Back up this entire directory, not just SQLite.

Brand assets and colour specifications: [BRAND.md](BRAND.md). Existing app IDs and repository URLs stay stable so preview updates preserve data.

## Licence

MPS — Media Payment System is available under the [MPS Source-Available No-Resale Licence](LICENSE). Business use and collecting membership payments are allowed; resale of the application requires separate written permission. The GitHub-distributed app includes ten members free, with larger installations requiring an active £5/month unlock subscription. Account data is retained when a subscription ends.
Copyright (c) 2026 matty03072.

Third-party dependencies retain their own licences.


### Subscription and application registrations

The £5/month unlock includes up to three registered applications. The purchase
and licence-management service is hosted separately on the publisher's Members
site. Licence customers use verified email/password accounts and mandatory MFA.
No publisher Stripe secret or private signing key is included in the client.

The client checks every 15 minutes and accepts only signed, application-bound
checks lasting at most one hour. If payment fails or the subscription ends, the
ten oldest enabled non-admin members retain MPS access and all other account
information remains stored. Administrators retain licence-management access.
Removing an application registration disables that application on its next check
(or by the current check's expiry offline), retaining its data. Removing a
registration does not cancel the subscription; billing is managed separately.

### Licensing release candidate

The licensing changes are a release candidate, not yet enabled for paid sales.
The licence site is https://members.streaminghome.app/mps. The client includes
only its HTTPS origin and public verification key, never server credentials.
See [application licensing](docs/APPLICATION-LICENSING.md) for activation,
limits, cancellation effects and upgrade preparation.

Existing preview installations with more than ten enabled non-admin members
should back up `/state` and read the new terms before updating. Once installed,
the licensing build enforces the free allowance until a valid subscription is
activated. Do not update a larger installation expecting an automatic paid unlock.
Earlier releases retain their original licence terms; this change is prospective.

The release preflight checks public-key configuration and matching terms only.
It does **not** prove payment, webhook, SMTP or activation availability. Paid sales
remain gated on integrated sandbox verification and published commercial policies.
