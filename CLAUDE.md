# Overdrive contributor context

Overdrive is a self-hosted CRM with a shared permission model for humans and external AI agents. Read [docs/session-guide.md](docs/session-guide.md) when starting a new session; no previous chat is required.

## Navigation

- [README](README.md): product and quick start; [documentation index](docs/README.md): human guides.
- [Architecture](docs/architecture.md): domain, trust boundaries and design decisions.
- [Application context](overdrive/CLAUDE.md): backend patterns and UI integration.
- [Script context](scripts/CLAUDE.md): operational entry points and side effects.
- [Test context](tests/CLAUDE.md): fixtures and regression map.
- `migrations/`: immutable applied schema changes; `schemas.surrealql` is the consolidated reference.
- `.github/workflows/`: tests and release-image automation. Publication does not deploy running stacks.

## Project-wide constraints

- Public code, documentation and UI copy are in English. Keep changes focused and preserve the compact Pitlane interface.
- Browser, API and MCP changes must share authorization and business behavior. Do not replace the CRM service with direct record access in a new surface.
- Preserve record-version checks, audit history, idempotency and business-unit isolation. Use the documented migration workflow for schema changes.
- Version/dependency truth is in manifests and lockfiles; release history is in `CHANGELOG.md`. Refresh install examples when changing the release image pin.
- `.env`, `.local/`, backups, exports, screenshots with real CRM data and deployment credentials are private. Do not add them to commits or release assets.
- If `.local/OPERATIONS.md` exists, read it before operating this checkout's installations. It is optional private deployment context, not a dependency for a fresh clone. Recheck running state; local and deployed environments can differ.
- A release is not complete until its CI and image publication succeed. A deployed fix is not verified merely because the image was published. See [development](docs/development.md) and [operations](docs/operations.md).
