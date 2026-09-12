# Overdrive

**A self-hosted CRM for humans and AI agents.** Keep the story moving.

Overdrive keeps your pipeline, relationships, next steps and deal context together. People work in a compact web interface; agents use the same permissions through an authenticated MCP server. Your existing agents write proposals and pricing; Overdrive stores their work without prescribing how they produce it.

[![CI](https://github.com/lfnovo/overdrive/actions/workflows/ci.yml/badge.svg)](https://github.com/lfnovo/overdrive/actions/workflows/ci.yml)
[![MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

## What you get

- Six-stage pipeline with drag and drop, won/lost outcomes and reversible archiving.
- Business units with membership-based access and shared contacts and organizations.
- **Deal so far:** an editable Markdown summary alongside notes and an audit trail.
- Tasks, source tracking, files and links, Markdown/PDF previews and outdated markers.
- Inline editing, contextual drawers and keyboard shortcuts.
- Pipeline analytics based on observed history, with gaps in imported data made explicit.
- MCP over Streamable HTTP with browser authorization, PKCE and revocable tokens.

Built with Python, FastAPI, Jinja/HTMX and **SurrealDB 3.2.0**. Files use a persistent filesystem volume. The Python SDK version is 2.0.0; that is separate from the database server version.

## Try it locally

Requires Docker Compose and Python 3 to generate configuration. This starts a fresh workspace with real local-password authentication:

```sh
git clone https://github.com/lfnovo/overdrive.git
cd overdrive
python3 scripts/configure_local.py --admin-email you@example.com --docker
docker compose up -d --wait
docker compose run --rm --no-deps overdrive python scripts/activate_user.py \
  --email you@example.com --expect-ns overdrive --expect-db app
```

Open the one-use activation link printed by the last command, choose your password, then sign in at **http://localhost:18765** with your email. The Compose port binds to loopback only. Credentials are generated locally; existing `.env` files are never overwritten.

Release images are available at `ghcr.io/lfnovo/overdrive:0.1.2` and `:latest`, for Linux AMD64 and ARM64. The Compose example pins the application and SurrealDB versions. To build the application yourself, use `docker compose up -d --build --wait`.

Both database records and uploaded files survive container recreation through named volumes. `docker compose down` preserves them; **`docker compose down -v` deletes them**.

## Sign in

Local email/password sign-in is built in. Generate a one-use activation link for the initial administrator with `scripts/activate_user.py`, then invite teammates through Settings. Google sign-in is optional. See [authentication and account recovery](docs/authentication.md). For a shared installation, set `APP_URL` to the public HTTPS origin and disable both development-login flags.

See [installation and Google setup](docs/installation.md), including reverse-proxy guidance. This is an early release: automated tests cover application authorization and the MCP OAuth exchange, but a real Google-provider login still needs deployment-specific validation.

## Connect an agent

Add `https://your-overdrive.example/mcp` as a remote MCP server in a client that supports Streamable HTTP and OAuth. The client opens a browser for login and consent. Agents inherit the signed-in user's access, including business-unit isolation.

See [MCP usage](docs/mcp.md) for tools, version checks, idempotency and file transfers.

Use version **0.1.1 or newer** for MCP through a public domain. Version 0.1.0 could complete OAuth but reject the MCP connection with HTTP 421.

## Documentation

- [Authentication and account recovery](docs/authentication.md)
- [Using Overdrive](docs/usage.md)
- [Configuration and installation](docs/installation.md)
- [Portainer with an existing database](docs/portainer.md)
- [Database connections and migrations](docs/database.md)
- [Backup, restore and upgrades](docs/operations.md)
- [Development and tests](docs/development.md)
- [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Changelog](CHANGELOG.md)

The initial version is intentionally opinionated: one company per installation, one fixed pipeline, local or Google identity, BRL values and São Paulo date handling. It does not host or run AI agents. See the [documentation index](docs/README.md) for current boundaries.

## License

MIT © Luis Novo. Vendored frontend license notices are included in [third_party](third_party/).
