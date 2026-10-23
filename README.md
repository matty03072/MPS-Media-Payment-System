# Members Portal — umbrelOS Preview

Test build published by matty03072. This is not an official Umbrel App Store release.

A self-hosted media membership portal with optional Emby, Ombi, SMTP and billing
integrations. Use test accounts only; no service is configured automatically.

Community test store: https://github.com/matty03072/members-portal-preview-store

## First launch
Open the app through Umbrel, unlock `/setup` using `owner` and the password
shown by Umbrel, then create your permanent owner account and enrol MFA.
The default Umbrel proxy protects all routes. Public invitations and external
payment callbacks require a separately verified deployment and are not enabled
by this preview. Never reuse production databases or credentials for testing.

## Development
Install requirements.txt, then run:

```sh
python -m unittest installer.test_installation installer.test_umbrel.UmbrelTests -q
```

Images target linux/amd64 and linux/arm64. Configuration, database and encryption
keys persist together in `/state`. Back up this entire directory, not just SQLite.
No open-source licence has been selected; publication does not grant an additional
licence. Third-party dependencies retain their own licences.
