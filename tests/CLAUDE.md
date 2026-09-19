# Regression tests

Tests exercise real SurrealDB behavior and the application through an in-process ASGI client.

## Fixtures and boundaries

- `conftest.py` creates a unique `overdrive_test_*` namespace, database-scoped user and current migrations per `system` fixture; teardown removes that namespace. Uploads use a temporary directory.
- Supply a disposable server and test credentials using [development](../docs/development.md). The fixture needs root privileges to create/remove namespaces; app operations use the scoped user.
- `system` defaults to local simulated login. Indirect parametrization supplies a different APP_URL; HTTPS tests must use real local-password activation/login because development login is disabled for HTTPS.
- Do not run these fixtures concurrently in one process: the database library configuration has a single active owner. Do not point browser smoke scripts at a real workspace.
- Helpers in `test_journey.py`, `test_oauth_mcp.py` and `test_local_auth.py` create fictional records, authorize test clients and activate passwords. Never import private fixtures or production credentials.

## Coverage map

| Area | Tests |
| --- | --- |
| Shared access and mutations | `test_journey.py`, `test_security.py`, `test_transfer.py` |
| Passwords and development identity | `test_local_auth.py`, `test_dev_identity.py` |
| MCP/OAuth and public origins | `test_oauth_mcp.py`, `test_mcp_public_origin.py` |
| Task inbox and deal outcomes | `test_my_tasks.py`, `test_deal_status.py`, `test_stages.py` |
| UI contracts and copy | `test_contextual_editing.py`, `test_ui_voice.py` |
| Files, previews and backup | `test_materials.py`, `test_preview.py`, `test_backup.py` |
| Database lifecycle | `test_migrations.py`, `test_database_refresh.py` |
| Analytics and sources | `test_analytics.py`, `test_sources.py` |
| Importers | `test_importer.py`, `test_pipedrive_import.py`, `test_pipedrive_files.py` |

For a regression, reproduce the externally visible failure, assert the corrected result and cover permission/transaction consequences where relevant. HTTP response tests do not verify browser interaction; use the disposable Playwright journeys in `scripts/` for that.
