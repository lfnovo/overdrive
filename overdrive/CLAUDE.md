# Application implementation

The same CRM service implements commercial behavior for browser forms, HTTP API and MCP clients.

## Module map

- `app.py`: application factory, lifespan, browser authentication, web/API routes, rendering and file previews.
- `service.py`: `Actor`, `DomainError`, access predicates, listing, validation, versioned transactions, history and idempotency.
- `models.py`, `stages.py`: accepted commercial fields, record references and fixed pipeline values.
- `database.py`, `migrations.py`: SurrealDB adapter, connection ownership and guarded migration runner.
- `auth.py`, `local_auth.py`: OAuth grants/tickets and local password/account lifecycle. These are separate from generic CRM CRUD.
- `mcp_server.py`: authenticated tool adapters, public host/origin validation and transport.
- `storage.py`, `backup.py`: immutable file bytes, deletion/restore cleanup and portable archives.
- `analytics.py`: metrics from observed history; missing imported history stays explicit.
- `importer.py`, `pipedrive_import.py`, `material_migration.py`, `stage_migration.py`: import and legacy conversion logic, invoked through maintenance scripts.
- `config.py`, `voice.py`: environment settings and session-stable decorative copy.
- [templates/](templates/CLAUDE.md): UI composition and integration with handwritten JS/CSS in `static/`.

## Patterns and traps

- Keep permission and business filters in `CRM`, so UI/API/MCP agree. Authenticated users are rechecked for active status and current unit memberships. See [architecture](../docs/architecture.md) for the access model.
- Commercial writes go through the service's validation and transaction paths. `_commit` atomically stores changes, audit events and idempotency receipts, with record-version comparisons. Handle 409 by rereading the current record; do not silently retry with a newer version.
- `Database.query` returns every statement result and checks every error; `rows` returns the last result. `ensure_record_id` validates references; bind values instead of interpolating user input into SurrealQL.
- `surreal-basics` configuration is process-global. One active `Database` instance owns it; commands targeting another database run separately. Its lower-level convenience helpers do not replace this adapter's multi-statement validation.
- App startup connects and may create an initial administrator; it does not migrate. Never start a fresh destination app before a portable restore.
- Some legacy deals lack `deal_so_far`. Full-content writes explicitly fill its empty default; schema defaults do not backfill old records.
- `mine=True` task listing means the user's work on open, non-archived deals, filtered before pagination. Per-deal history still exposes its tasks. See [usage](../docs/usage.md).
- Uploads are filesystem-backed, separate from metadata. Keep checksum validation and cleanup on failure. Preview/download access must pass deal authorization.
- Do not add `credential`, `auth_record` or other authentication internals to `MODELS` or generic MCP CRUD.

Follow [database migration rules](../docs/database.md) for schema changes and [tests guidance](../tests/CLAUDE.md) for regression coverage.
