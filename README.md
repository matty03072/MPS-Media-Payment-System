![MPS — Media Payment System](static/mps-logo.svg)

# MPS — Media Payment System

**One place to manage media memberships, payments and connected service accounts.**

MPS is a self-hosted application that brings **Emby, Emby Connect, Ombi, Stripe, PayPal and email** together in one member portal and administrator dashboard. It connects the journey from invitation and signup to account creation, subscriptions and media access—reducing the need to manage every member separately in each service.

Members can manage their membership, open Emby, discover and request content through Ombi, and manage their account security. Administrators can invite members, choose their servers and libraries, configure plans, monitor account setup and track revenue from the same application.

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
| **Stripe and PayPal** | Optional subscription checkout, payment-status reconciliation and cancellation workflows connected to membership entitlement. |
| **SMTP email** | Branded invitations, verification, password recovery, account notifications and optional media-availability messages. |
| **Background worker** | Account provisioning, access reconciliation, retries and queued account-maintenance tasks. |

MPS coordinates these services through their APIs; it does not replace the media server, request engine or payment provider. Administrators supply their own service URLs, credentials and settings before automation can run.

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

Add the [MPS community store](https://github.com/matty03072/members-portal-preview-store) to umbrelOS, then install **MPS (Preview)**. Existing preview users should update the installed app rather than uninstall it.

On first launch, unlock `/setup` with username `owner` and the password shown by Umbrel, create your permanent owner account and enrol MFA.

### Preview scope

Use test accounts and integrations. The default Umbrel proxy protects every route, so public invitations and external payment callbacks require a separately configured and verified public HTTPS deployment; they are **not enabled by the default preview package**. Automated account creation also requires configured services, a running worker and any required membership entitlement.

Container tests do not prove every third-party service works in your environment. Complete an end-to-end test with your own services before relying on the automation. No production database, service credentials or member records are included.

## Development

Install `requirements.txt`, then run:

```sh
python -m unittest installer.test_installation installer.test_umbrel.UmbrelTests installer.test_branding.BrandingTests -q
```

Images target `linux/amd64` and `linux/arm64`. Configuration, database and encryption keys persist together in `/state`. Back up this entire directory, not just SQLite.

Brand assets and colour specifications: [BRAND.md](BRAND.md). Existing app IDs and repository URLs stay stable so preview updates preserve data.

No open-source licence has been selected; publication does not grant an additional licence. Third-party dependencies retain their own licences.
