# Changelog

## 0.1.3

- Fix MCP clients losing their connection and asking for authorization again after the computer slept, the network dropped during a token refresh, or several sessions shared one connection. A refresh token that has already been exchanged now remains usable for 30 minutes, so a client that never received the response can retry.
- Refreshing no longer invalidates the access token issued before it. That token remains valid until its own one-hour expiry, so other sessions using the same connection keep working.
- Revoking a connection, changing or recovering a password, and deactivating a user still invalidate every token immediately.

No database migrations or credential changes are required for this update. Clients that already lost their connection must authorize once more after upgrading.

## 0.1.2

- My tasks now excludes tasks on won, lost and archived deals, in both Pending and Completed views. The same filter applies to the MCP `my_tasks` tool and API task listings with `mine=true`, before pagination.
- Historical tasks remain available on their deals and return to My tasks when the deal is open and non-archived again. Their completion status is preserved.

No database migrations or credential changes are required for this update.

## 0.1.1

- Fix MCP connections through a public domain returning HTTP 421 after successful OAuth authorization. The MCP transport now accepts the host and origin configured in `APP_URL`, while continuing to reject untrusted hosts and origins.
- Add regression coverage for password login, OAuth, MCP initialization and tool discovery on HTTPS domains, including custom ports.

No database migrations or credential changes are required for this update.

## 0.1.0

Initial public release of Overdrive, a self-hosted CRM for humans and AI agents.

- Six-stage deal pipeline, drag and drop, won/lost outcomes, archiving and inline editing.
- Business-unit permissions, contacts, organizations, tasks and deal sources.
- Editable deal summaries, Markdown notes and a change history.
- Uploaded files and links with Markdown/PDF previews and outdated markers.
- Pipeline analytics derived from observed history.
- Local email/password authentication, one-use activation and administrator recovery links, optional Google login.
- Authenticated MCP over Streamable HTTP with PKCE, scoped user access and revocable grants.
- SurrealDB 3.2.0, versioned migrations through surreal-basics, and portable backup/restore.
- MIT license, Docker Compose, CI tests and versioned multi-platform GHCR images.

### Current limitations

One company per instance, a fixed pipeline, BRL values and São Paulo date handling. Uploads use a filesystem volume. Google callback behavior has automated coverage, but a live provider login requires deployment-specific verification. The application does not run its own AI agents or sync email. See [current boundaries](docs/README.md).
