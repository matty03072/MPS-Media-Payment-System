# Preview changes

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
