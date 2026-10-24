# Application licensing

## Software terms and subscription entitlement

The [software licence](../LICENSE) permits personal and business operation,
including charging your own members. Reselling MPS or a rebranded derivative
requires separate written permission. This is source-available software, not an
OSI-approved open-source licence. Third-party licences and rights already granted
for older releases remain unchanged.

An MPS subscription is separate from the Stripe/PayPal subscriptions you collect
from your own members. It costs £5 per month and permits three concurrent
registered MPS applications. The free tier supports ten enabled non-admin members.
Administrators do not count towards that allowance.

## Activation

1. Create a licence account at https://members.streaminghome.app/mps.
2. Verify your email and enrol an authenticator for mandatory MFA.
3. When sales open, accept the displayed terms and purchase the monthly subscription.
4. After payment verification, an unlock code is emailed automatically.
5. In your installation, open **Licence** and submit that code. Each installation
   has a persistent UUID. Preserve `/state` when updating or migrating.

Never publish your unlock code. Your app contacts the publisher's HTTPS licence
service; it verifies a short-lived Ed25519-signed lease bound to its UUID.
Production clients reject sandbox leases. The client does not contain the
publisher's private signing key, Stripe credentials or customer database.

## Checks, fallback and removals

- Check-in is normally every 15 minutes; a valid lease lasts at most one hour.
- A temporary connection failure uses the existing valid lease. Once it expires,
  the application returns to the ten-member tier.
- Failed payment or an ended subscription also returns the app to the ten-member
  tier. The ten oldest enabled non-admin members retain MPS access. Other account
  records remain stored; they are not deleted.
- This MPS licence fallback does not itself disable linked Emby/Ombi accounts.
  Your own membership entitlement rules continue separately.
- A fourth registration is refused. Remove an old application in your licence
  account to free a slot. Removal disables that old application on its next
  check; an offline instance cannot retain paid access beyond lease expiry.
- A received disabled state remains disabled after its lease expires. Normal
  app routes and worker activity stop; licence recovery and data remain available.
- Removing a registration is **not** subscription cancellation. Use billing
  management in the licence account for subscription changes.

Application UUIDs, first/latest connection IPs, check-in times and registration
history are held by the licence service to manage registrations. Commercial
seller/support, refund/cancellation and privacy disclosures must be published
before paid sales are enabled.

## Upgrades

Back up the complete `/state` directory (database, keys and installation identity)
before installing the licensing build. Do not delete/reinstall to upgrade.
Installations over the free allowance need an active unlock subscription to keep
all enabled members' MPS access. Review these changes before upgrading.

## Current release status

This is the licensing release candidate. Paid sales are disabled. Automated tests
cover signatures, invalid/test/expired leases, member boundaries, MFA/CSRF,
webhook replay, email retries and registration limits. Those checks do not replace
an integrated real Stripe sandbox purchase-to-email-to-activation verification.
