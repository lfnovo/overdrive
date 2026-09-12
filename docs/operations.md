# Persistence, backup, restore and upgrades

The Compose installation has two named volumes: `database` for SurrealDB 3.2.0 and `attachments` for uploaded bytes. Keep both. `docker compose down` preserves them; `docker compose down -v` deletes them. `.env` contains your installation configuration and secrets; store it separately and privately.

## Portable backup

Stop application writes while taking a backup, so file bytes and database records stay consistent. Leave SurrealDB running. From the installation directory:

```sh
umask 077
mkdir -p backups
docker compose stop overdrive
docker compose run --name overdrive-backup --no-deps overdrive \
  python scripts/backup.py backup /tmp/overdrive.zip
docker cp overdrive-backup:/tmp/overdrive.zip backups/overdrive.zip
docker rm overdrive-backup
docker compose start overdrive
```

Use an unused destination name for each backup. The archive contains commercial records, password hashes and checksummed attachment bytes. It excludes OAuth grants, browser sessions, activation links, migration metadata and configuration. Keep archives private; a database hash is still sensitive. Verify that the command and copy succeeded before removing the helper container. If backup fails, inspect the error before restarting writes.

Native equivalent, with the app stopped:

```sh
PYTHONPATH=. uv run python scripts/backup.py backup .local/backups/overdrive.zip
```

## Restore to a fresh installation

Restore refuses a database containing existing application records. Never use it to merge into a running workspace. Start from a new directory containing the matching release's Compose file, source/configuration generator, and your archive at `backups/overdrive.zip`. Generate a new `.env`, or securely configure the new installation. Use a new Compose project name so existing volumes are untouched:

```sh
docker compose -p overdrive-restore up -d --wait surrealdb
docker compose -p overdrive-restore run --rm bootstrap
docker compose -p overdrive-restore run --rm -T --no-deps overdrive \
  sh -c 'cat > /tmp/restore.zip && python scripts/backup.py restore /tmp/restore.zip' \
  < backups/overdrive.zip
docker compose -p overdrive-restore up -d --wait
```

Do not start the app before restoring: it would create the initial administrator and make the target nonempty. The stream copies the archive into the helper container under the app's own UID, avoiding host bind-mount ownership differences. Bootstrap applies the schema before restoration. Change `OVERDRIVE_PORT` and `APP_URL` if the original instance still occupies the default port.

Sign in, check representative deals and previews, and reconnect agents. Password hashes survive the restore, while credential epochs change and invalidate old sessions. Older archives without local credentials require new activation links. Google identity bindings are preserved, but the restored installation needs its own valid OAuth configuration. Change `SESSION_SECRET` when restoring to another environment.

Native equivalent, after migrating an empty database and before starting the app:

```sh
PYTHONPATH=. uv run python scripts/backup.py restore backups/overdrive.zip
```

Test restoration periodically. Having an archive is not the same as having a verified recovery process.

## Upgrade

1. Read the release notes and save a backup while the old version is stopped.
2. Update the checked-out release and pin `OVERDRIVE_IMAGE` in `.env` to the new image version. Keep SurrealDB upgrades separate unless the release explicitly requires one.
3. Pull images and run bootstrap once. It applies pending migrations without changing an existing database user's password.
4. Start the new application and verify sign-in, the pipeline, a file preview and an MCP connection.

```sh
docker compose pull
docker compose run --rm bootstrap
docker compose up -d --wait
```

Use one migration process at a time. The app does not migrate automatically at startup. See [database migrations](database.md) for explicit commands and legacy adoption. Returning to an old image does not undo schema changes; restore the matching backup into a fresh installation when a rollback requires it.

## Health and troubleshooting

- `GET /health` checks database connectivity. It is a readiness signal, not a permissions or end-to-end test.
- `docker compose ps` shows the app and database health; the successful bootstrap container exits with code 0.
- `docker compose logs --tail 100 overdrive bootstrap surrealdb` shows startup failures. Never post unredacted logs or `.env` files publicly.
- Files live in the `attachments` volume, not inside the application image and not in SurrealDB BLOB storage.
- Password recovery and activation commands are documented in [authentication](authentication.md).
