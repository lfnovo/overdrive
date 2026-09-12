# Database connections and migrations

Overdrive uses `surreal-basics==0.8.1` with the SurrealDB Python SDK 2.0.0 and SurrealDB 3.

## Connections

The application delegates persistent connections, token renewal and connection cleanup to `surreal-basics`. Existing settings still work: `SURREAL_URL`, `SURREAL_NAMESPACE`, `SURREAL_DATABASE`, `SURREAL_USER` and `SURREAL_PASS`. They are loaded from `.env` by Overdrive and passed explicitly to the library. Authentication is scoped to the database; root credentials are only used for initial provisioning.

`overdrive/database.py` keeps the application's result format, strict record-ID validation and full multi-statement error checks. Commercial changes continue to use the existing atomic transactions, record versions, audit events and idempotency keys. Writes are not automatically replayed by this adapter after an ambiguous network failure.

The library has process-global configuration. Overdrive permits one active `Database` owner per process and rejects attempts to retarget it. Run migrations, imports and backups as separate commands. Multiple application processes can independently connect to the same database. Do not call `surreal_basics.init()` from request handlers.

## New installations

Configure `.env` using `.env.example`, then provide `BOOTSTRAP_USER` and `BOOTSTRAP_PASS` through the environment:

```sh
uv sync --locked
PYTHONPATH=. uv run python scripts/bootstrap.py
```

Bootstrap provisions the namespace, database and database-scoped service user, then runs pending migrations using that service user. The app creates the initial administrator when it starts. Bootstrap does not reset credentials for a service user that already exists.

## Existing installations without migration tracking

Stop the app and make a backup before adoption:

```sh

# Take a backup using the existing installation's compatible backup command first.
PYTHONPATH=. uv run python scripts/migrate.py baseline --expect-ns overdrive --expect-db local
PYTHONPATH=. uv run python scripts/migrate.py status --expect-ns overdrive --expect-db local
```

Use your actual namespace and database in the target flags. The command fails before connecting if the configured target differs.

Baseline verifies every table, field and index definition declared in migration 001 against the existing schema, then uses `AsyncMigrationRunner.baseline(target_version=1)` to record it as applied. It does not execute the migration SQL or rewrite commercial records. A mismatch requires investigation; do not bypass it by marking an incompatible schema as current. Already-recorded migrations are skipped.

`up` refuses an existing schema without migration history. It must be baselined first, then upgraded with `scripts/migrate.py up`. A pre-authentication database does not contain the credential table required by the new portable backup command; use its old application version to take the pre-upgrade backup. This also applies to re-running bootstrap against a legacy installation.

## Updating

After taking a backup, stop the app and run exactly one migration process before starting the new version:

```sh
PYTHONPATH=. uv run python scripts/migrate.py up --expect-ns overdrive --expect-db local
PYTHONPATH=. uv run python scripts/migrate.py status --expect-ns overdrive --expect-db local
```

The app does not automatically run migrations at startup. Tracking lives in `_sbl_migrations`; it is local installation metadata, not commercial backup content. Restore commercial backups into a fresh, migrated database using the documented backup command.

## Adding a migration

Create a numerically ordered `.surrealql` file under `migrations/`, for example `003_add_deal_field.surrealql`. Never edit an applied migration. `schemas.surrealql` is a consolidated schema reference; bootstrap and tests use the migration directory. Keep it aligned when adding schema changes.

Overdrive uses `AsyncMigrationRunner.run_up()` directly. Version 0.8.1 validates every statement result before recording the migration, including failures after a successful `BEGIN`. Execution and tracking are still separate operations. Use the Overdrive wrapper to retain the target and baseline guards. Use idempotent SQL and a single migration process. For a multi-statement change that must be atomic, include `BEGIN TRANSACTION;` and `COMMIT TRANSACTION;` in the file. A failed or interrupted migration may need investigation before retrying. Test both a fresh install and an upgrade with existing records.

There is intentionally no down migration for the initial schema: dropping it would destroy data. Returning to an older application image does not reverse schema changes; recovery may require restoring the matching backup.

The library’s dry-run currently wraps each file in a transaction, so files containing their own `BEGIN/COMMIT` cannot be validated that way ([upstream #37](https://github.com/lfnovo/surreal-basics/issues/37)). The Overdrive workflow tests migrations against disposable databases instead.

## Validation

Tests provision disposable namespaces and initialize them with the same migration runner as installations:

```sh
BOOTSTRAP_USER=root BOOTSTRAP_PASS=root uv run pytest -q
```

The suite checks connection renewal, atomic commercial changes, permissions, backup/restore, repeatable migrations, safe legacy adoption and failure without advancing the migration version.
