# Installation

## Docker Compose

Use the repository's `compose.yaml`. It runs the Overdrive image, SurrealDB **3.2.0** with RocksDB persistence, and a one-shot bootstrap that creates the database-scoped application user and applies migrations. SurrealDB runs as its image's nonroot user; the named volume is mounted at its writable home directory. The app runs as UID 10001.

Generate configuration once:

```sh
python3 scripts/configure_local.py --admin-email you@example.com --admin-name "Your Name" --docker
```

Use [local password activation](authentication.md) without configuring Google. For a simulated development identity, add `--dev`. Run `docker compose up -d --wait` and open http://localhost:18765. Use `docker compose logs overdrive bootstrap` to diagnose startup errors.

The Compose file reads individual variables; it does not pass root database credentials to the running application. The database port is internal to the Compose network. Only the app is published, on host loopback. The generator does not overwrite an existing `.env`.

If your database and uploaded files are already restored, see [Portainer with an existing database](portainer.md) for an app-only stack. A running SurrealDB server alone is not enough: the target database needs the application user and schema migrations first.

## Configuration

| Variable | Purpose |
| --- | --- |
| `APP_URL` | Exact external origin, without trailing slash; HTTPS for shared use. |
| `WORKSPACE_NAME` | Workspace branding shown in the UI. |
| `ADMIN_EMAIL`, `ADMIN_NAME` | Initial administrator for a fresh database. Existing users are not overwritten. |
| `SESSION_SECRET` | Random secret for session cookies; generate and keep private. |
| `LOCAL_LOGIN`, `GOOGLE_LOGIN` | Enable local passwords and/or Google sign-in. Both default to true; Google also requires credentials. |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google OAuth web application credentials. |
| `DEV_LOGIN` | Simulated login, default false. Requires a localhost APP_URL over HTTP. |
| `DEV_LOGIN_EMAIL` | Optional identity override for native local development. |
| `DEV_LOGIN_ALLOW_REMOTE` | Explicitly allow Docker bridge clients in local evaluation mode. Default false. |
| `SURREAL_URL` | Native-dev database URL; Compose sets it to its internal SurrealDB service. |
| `SURREAL_NAMESPACE`, `SURREAL_DATABASE` | Installation's namespace and database. |
| `SURREAL_USER`, `SURREAL_PASS` | Database-scoped service credentials. |
| `BOOTSTRAP_USER`, `BOOTSTRAP_PASS` | Administrative database credentials, used for provisioning only. |
| `STORAGE_PATH` | Filesystem directory for uploaded bytes; Compose uses `/data/attachments`. |
| `MAX_UPLOAD_BYTES` | Upload limit; default 25 MiB. |
| `OVERDRIVE_IMAGE` | Compose image override; default `ghcr.io/lfnovo/overdrive:0.1.1`. |
| `OVERDRIVE_PORT` | Compose host port; default 18765. |

The settings layer reads `.env` for native development. Compose explicitly forwards the variables it supports; add optional application settings to `environment` if needed. Keep database and attachment volumes together when moving an installation.

## Google OAuth

1. Create an OAuth consent screen and a **Web application** client in Google Cloud. Configure its audience for your organization or intended users. If the consent app is in testing, add the intended test users.
2. Add the exact redirect URI: `https://crm.example.com/auth/google/callback` (or `http://localhost:18765/auth/google/callback` for a local provider test).
3. Put the client ID and secret in `.env`.
4. Set `APP_URL=https://crm.example.com`, `DEV_LOGIN=false`, and `DEV_LOGIN_ALLOW_REMOTE=false`.
5. Ensure `ADMIN_EMAIL` matches the Google account on first installation. Restart the app with `docker compose up -d`.
6. Sign in and invite users by creating their email addresses under Settings. The application accepts only verified Google email identities for active, pre-provisioned users; it does not auto-enroll arbitrary Google accounts.

Google setup reference: https://developers.google.com/identity/openid-connect/openid-connect

## HTTPS and reverse proxy

Place a reverse proxy such as Caddy or nginx on the same host, proxying the public origin to `127.0.0.1:18765`. Terminate TLS there. `APP_URL` controls redirects and secure cookies. The container disables implicit proxy-header trust; no forwarded client address is needed for Google login. Development login must remain disabled on shared installations.

If the proxy is a separate container, join it to the app's Docker network and proxy to `overdrive:8000`. Do not expose the database publicly. The default Compose file is a single-host example; Kubernetes deployment is not included in this release.

Before inviting users, test sign-in with your enabled methods, an unauthorized account, sign-out, and an MCP client's browser-consent flow on the actual HTTPS origin. Google requires a live-provider check when enabled.

Open the public HTTPS URL when signing in. With an HTTPS `APP_URL`, session cookies are marked `Secure`; signing in through the backend's HTTP IP address will fail with “This form has expired.” The proxy-to-app connection can still use HTTP.

Keep the original public `Host` header when proxying MCP requests. Since 0.1.1, the MCP transport accepts the host and origin from `APP_URL` and rejects unrelated hosts and origins. Do not rewrite the host to localhost or disable the protection to work around a configuration mismatch.
