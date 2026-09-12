# Security

Overdrive is an early-stage, self-hosted application. Security fixes target the latest released version; upgrade promptly when a fix is available.

## Report a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/lfnovo/overdrive/security/advisories/new). Include affected versions, reproduction steps, impact and a minimal example with fictional data. Do not open a public issue containing exploit details or private records. If private reporting is unavailable, open an issue asking for a private reporting channel without disclosing the vulnerability.

Do not send credentials, production database dumps or personal data. There is no guaranteed response-time SLA.

## Operating safely

Use HTTPS for shared access, disable development login, keep SurrealDB private, and protect configuration, activation links and backups. Run the app with its database-scoped service account; root database credentials belong only in provisioning/administrative processes. Scope user access through business units and revoke unused agent connections.

See [authentication](docs/authentication.md), [installation](docs/installation.md) and [operations](docs/operations.md) for the supported setup, recovery and backup behavior. Automated tests are not a substitute for reviewing the network and identity configuration of your own installation.
