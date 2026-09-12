# Portainer with an existing database

This example runs only Overdrive on a Linux Docker Standalone host. It assumes SurrealDB 3 is already publishing port 8019 on that host, the target database has been migrated, and an attachment volume has been restored. For a fresh installation, use the full [installation example](installation.md), including bootstrap and administrator activation.

## Stack

Paste this into the Portainer stack editor:

```yaml
services:
  overdrive:
    image: ghcr.io/lfnovo/overdrive:0.1.2
    network_mode: host
    environment:
      APP_URL: ${APP_URL:?Set the public HTTPS origin}
      WORKSPACE_NAME: ${WORKSPACE_NAME:-Overdrive}
      SESSION_SECRET: ${SESSION_SECRET:?Set SESSION_SECRET}
      SURREAL_URL: http://127.0.0.1:8019
      SURREAL_NAMESPACE: ${SURREAL_NAMESPACE:?Set the restored namespace}
      SURREAL_DATABASE: ${SURREAL_DATABASE:?Set the restored database}
      SURREAL_USER: ${SURREAL_USER:-overdrive_app}
      SURREAL_PASS: ${SURREAL_PASS:?Set the application database password}
      STORAGE_PATH: /data/attachments
      LOCAL_LOGIN: "true"
      GOOGLE_LOGIN: "false"
      DEV_LOGIN: "false"
      DEV_LOGIN_ALLOW_REMOTE: "false"
    command: ["uvicorn", "overdrive.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "18765", "--no-proxy-headers"]
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:18765/health', timeout=4)"]
      interval: 10s
      timeout: 5s
      start_period: 20s
      retries: 5
    volumes:
      - attachments:/data/attachments
    restart: unless-stopped

volumes:
  attachments:
    external: true
    name: ${ATTACHMENTS_VOLUME:?Set the existing attachment volume name}
```

With `network_mode: host`, `127.0.0.1` refers to the Docker host's network, so it can reach port 8019 published by a separate SurrealDB stack. This differs from the default bridge network, where localhost refers to the individual container. The app listens on the host's port 18765; ensure that port is available.

## Environment variables

Set these in the stack's environment-variable section, or load them from a private environment file:

| Variable | Value |
| --- | --- |
| `APP_URL` | Your public HTTPS origin, such as `https://crm.example.com`, without a trailing slash. |
| `SESSION_SECRET` | A persistent random signing secret. Keep it stable across container recreation. Changing it invalidates existing browser cookies. |
| `SURREAL_NAMESPACE`, `SURREAL_DATABASE` | The exact namespace and database containing the restored data. |
| `SURREAL_PASS` | The password created for the database-scoped application user. This is not the root password or the Overdrive login password. |
| `ATTACHMENTS_VOLUME` | The existing restored Docker volume name. Its files must be readable and writable by UID 10001. |
| `SURREAL_USER` | Optional; defaults to `overdrive_app`. Match the user provisioned in the target database. |
| `WORKSPACE_NAME` | Optional workspace name; defaults to `Overdrive`. |

Portainer variables supply the `${...}` substitutions. The `environment` section forwards the selected values to the container. Fixed settings already present in the YAML do not need duplicate Portainer variables. The `x-app-environment` anchor in the full Compose example only shares settings between services; this single-service example does not need it.

Bootstrap provisions the database user and applies schema migrations. It is omitted here because that work is already complete; the app does not run migrations itself. This stack needs no `BOOTSTRAP_USER`, `BOOTSTRAP_PASS`, or initial-administrator settings. A restored account with a password can sign in with its existing password; restored sessions and MCP grants require reauthorization. See [backup and restore](operations.md) and [account activation](authentication.md).

## Proxy, verification and updates

Configure Caddy or another TLS proxy to forward your public HTTPS domain to the host's port 18765. If Caddy runs directly on the host or uses host networking, the upstream can be `127.0.0.1:18765`. A proxy in a bridge-networked container needs an address that reaches the Docker host. Preserve the public Host header and access the UI through the HTTPS domain so its secure cookies work.

After deploying, check `/health`, sign in, open a restored deal and a file preview, then connect an agent to `https://crm.example.com/mcp` and call `whoami`. See [MCP troubleshooting](mcp.md).

For updates, read the release notes, back up the database and files, change the image tag, and update the stack to pull the image and recreate the container. Versions 0.1.1 and 0.1.2 require no database changes. Future releases may require a separate migration step before starting the app; follow the [upgrade procedure](operations.md). Do not delete or replace the restored volume when updating the stack.
