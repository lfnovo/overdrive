# Development and release workflow

Use Python 3.12 or newer, `uv`, Node.js 22+ for frontend tooling, and SurrealDB **3.2.0**. The application is Python/FastAPI with server-rendered Jinja templates, HTMX and a small amount of JavaScript. Node.js is not required to run the application image.

## Native development

```sh
uv sync --locked
npm ci
python3 scripts/configure_local.py --admin-email admin@example.test --admin-name "Demo Admin" --dev
```

Provide a local SurrealDB 3 instance. For a disposable in-memory database:

```sh
docker run --rm --name overdrive-dev-db -p 127.0.0.1:8019:8000 \
  surrealdb/surrealdb:v3.2.0 start --bind 0.0.0.0:8000 \
  --user root --pass local-development-only memory
```

Set `BOOTSTRAP_PASS=local-development-only` in your generated `.env` for this disposable database. If using an existing server, use its administrative credentials instead. Export bootstrap credentials for the CLI, which reads them from the environment:

```sh
set -a
. ./.env
set +a
PYTHONPATH=. uv run python scripts/bootstrap.py
uv run uvicorn overdrive.app:create_app --factory --host 127.0.0.1 --port 18765 --reload
```

Only source a configuration file you created and trust. Open http://localhost:18765. Development login is an explicit local shortcut; use `DEV_LOGIN=false` and the [activation flow](authentication.md) to test actual passwords.

## Automated checks

Use a disposable SurrealDB server. Integration tests create uniquely named namespaces and remove them afterward. They require root credentials for creating those namespaces; application operations still use a database-scoped user.

```sh
uv run ruff check overdrive tests scripts
BOOTSTRAP_USER=root BOOTSTRAP_PASS=local-development-only uv run pytest -q
```

Tests cover permissions, record versions, atomic updates, analytics, migrations, material previews, backup/restore, local authentication, and MCP registration/PKCE/consent/token rotation/revocation. Google callback behavior is mocked; a live provider test remains an installation-specific step.

CI also starts a fresh Compose stack with `DEV_LOGIN=false`, generates an activation link, and runs Playwright through activation, sign-in, password change/recovery, deal creation and inline editing. Browser scripts must only target disposable installations because they create and modify records. They are under `scripts/smoke_auth.cjs` and `scripts/smoke_browser.cjs`.

## Frontend assets

Vendored assets make the app independent of frontend CDNs at runtime. After changing Tailwind classes or upgrading vendored dependencies:

```sh
npm run build:css
npm run vendor
```

Commit the resulting assets and lockfile. `overdrive/static/pitlane.css` contains the interface's custom styling. Keep frontend third-party license notices in `third_party/`.

## Code map

- `overdrive/app.py`: web routes, templates, browser sessions and HTTP adapters.
- `overdrive/service.py`: shared CRM behavior and authorization for UI/API/MCP.
- `overdrive/auth.py`, `local_auth.py`: agent OAuth grants and local credentials.
- `overdrive/database.py`, `migrations.py`, `migrations/`: database connections and versioned schema.
- `overdrive/storage.py`, `backup.py`: immutable file bytes and portable archives.
- `overdrive/templates/`, `static/`: server-rendered interface and browser behavior.
- `tests/`: unit and integration coverage.

Keep credentials out of generic CRM models and APIs. Preserve business-unit isolation in every new read/write path. Use the database adapter's record-ID validation and parameter binding. Never edit an applied migration; add a new file and test both fresh installation and upgrade paths.

## Release

1. Update the package version, lockfile, Compose image pin, README examples and changelog.
2. Run CI on the intended commit and review the changes.
3. Publish a GitHub release tagged `vMAJOR.MINOR.PATCH` on that commit.
4. The release workflow reruns CI, then builds and publishes Linux AMD64/ARM64 images to `ghcr.io/lfnovo/overdrive:MAJOR.MINOR.PATCH`.
5. Stable releases also update `latest` when GitHub identifies that release as the latest stable release. Prereleases receive only their version tag.
6. Verify both published platforms and an anonymous image pull before announcing availability.

A failed verification job prevents image publication. GitHub Actions uses the repository's `GITHUB_TOKEN` with package-write permission; no registry password is stored in the repository. The first package publication may require making its visibility public in GitHub Packages settings. This workflow publishes images only; it does not deploy or modify running installations.
