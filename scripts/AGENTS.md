# Operational entry points

These commands use application services and installation settings; several can modify real data.

## Commands

- `configure_local.py`: generate configuration with exclusive file creation; never replace an existing `.env` or print secrets.
- `bootstrap.py`: root-level provisioning of the configured target, then scoped schema migrations. Existing service-user passwords are not reset.
- `migrate.py`: guarded `status`, `baseline`, `up`; expected namespace/database flags are required.
- `activate_user.py`: issue a one-use account activation/recovery link, revoking prior password access and sessions. Expected target flags are required.
- `backup.py`: export/restore commercial records and files; restore requires an empty, migrated database and stopped app.
- `import_csv.py`, `import_pipedrive.py`, `import_pipedrive_files.py`: [import procedures](../docs/imports.md), validation/download by default and writes with `--apply`.
- `migrate_materials.py`, `migrate_stages.py`: legacy data conversions, separate from numbered schema migrations. They write immediately and have no dry-run switch.
- `verify_mcp.py`: local-development OAuth/PKCE + Python SDK probe; uses simulated login, lists tools/calls `whoami`, and revokes the temporary access token. Not a production login mechanism.
- `smoke_auth.cjs`, `smoke_browser.cjs`: Playwright journeys that change passwords or create/modify CRM data. Disposable installations only; see CI and the development guide.

## Integration constraints

Run Python scripts from the repository root with the project environment and `PYTHONPATH=.`; Docker supplies that path already. See [development](../docs/development.md) and [operations](../docs/operations.md) for exact commands.

Settings reads `.env`, but root bootstrap credentials are read from process environment. Use dotenv-aware loading, not shell sourcing. Confirm URL, namespace, database and storage path before mutation; scripts other than migration/activation do not all enforce expected-target flags. Do not infer a safe target from `localhost` alone.

Keep source exports, generated reports and secrets under ignored private storage. Imports have different retry/atomicity semantics; do not describe every importer as an all-or-nothing transaction.
