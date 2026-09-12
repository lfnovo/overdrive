# Authentication

Overdrive supports local email/password sign-in and optional Google sign-in. Both identify the same workspace user and apply the same business-unit permissions. There is no public registration and no shared default password.

## Sign-in methods

Set `LOCAL_LOGIN=true` for local passwords (the default). Set `GOOGLE_LOGIN=true` and configure `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` to show Google sign-in. Set either flag to `false` to disable that method, including its callback endpoints. At least one method must be usable before disabling development login. These flags control new sign-ins; they do not revoke existing sessions by themselves.

`DEV_LOGIN` is only a local development shortcut. It must be disabled for a deployed instance. Passwords and activation links require HTTPS outside local development. Keep the externally visible `APP_URL` correct; it is used to construct links and OAuth redirects.

## First administrator

Apply migrations first. The configured `ADMIN_EMAIL` and `ADMIN_NAME` create an administrator only when the user table is empty. Generate their activation link from the server:

```sh
PYTHONPATH=. uv run python scripts/activate_user.py \
  --email admin@example.com --expect-ns overdrive --expect-db app
```

For Compose, after the database bootstrap completes:

```sh
docker compose run --rm --no-deps overdrive python scripts/activate_user.py \
  --email admin@example.com --expect-ns overdrive --expect-db app
```

Use the email and database names from your configuration. The command prints a sensitive, one-use link valid for 24 hours. Open it privately, choose a password, then sign in. It never sets or prints a password. It can also recover access for an existing active account when the administrator cannot sign in. Server shell access is therefore privileged.

## Invite a teammate or recover access

1. An administrator creates the person in **Settings → People & access**, assigning their business units.
2. Select **Set up / reset password** next to that person.
3. Review the effects, then select **Generate activation link**.
4. Share the displayed link privately with the intended person. Overdrive does not send email and requires no SMTP configuration.
5. The person opens it, chooses and confirms their password, then signs in.

Generating a link immediately disables the previous local password, invalidates earlier activation links, and revokes existing browser sessions and MCP connections. Google sign-in remains possible when enabled. An inactive user cannot activate or sign in. The link is shown only once; the database stores its hash, never the link itself. If it expires or is lost, generate another.

Passwords accept 15–128 characters, including spaces. Password managers, autofill and pasting are supported. There are no arbitrary character-composition rules. **Settings → Your password** lets a person change an existing password by confirming the current one. This keeps their current browser signed in and revokes other sessions and agent connections.

## Agents

MCP uses its existing HTTPS OAuth/PKCE flow. The person signs in through the browser with an enabled method and approves the agent. The agent receives scoped tokens, never the person's password or activation link. Recovery and password changes invalidate access tokens, refresh tokens, pending authorization codes, and issued file-transfer tickets for that user. Reconnect the agent afterward.

## Security and operations

- Password hashes use Argon2id (64 MiB, three iterations, one lane) in a separate `credential` table, inaccessible through CRM CRUD and MCP tools.
- Password checks run off the event loop with bounded concurrency.
- Sign-in attempts are limited per account (10/15 minutes) and connection IP (60/15 minutes), using persistent counters shared across workers. Limits include successful attempts. Activation and password-change paths have separate limits.
- The app does not trust client-supplied forwarding headers. Behind a reverse proxy, clients may share the proxy's IP limit; configure and test your trusted proxy boundary before larger deployments.
- Browser sessions expire after 24 hours, are revocable server-side, and use HTTP-only signed cookies (`Secure` with HTTPS). Logout revokes that browser session.
- Authentication forms require CSRF tokens. Pages are not cached and use `Referrer-Policy: no-referrer`. Application access logs strip query strings; reverse proxies must also avoid logging activation-link query strings.
- Portable backups preserve password hashes but exclude activation links and OAuth/browser sessions. Restore generates fresh credential epochs, so old sessions cannot be reused. Older backups have no password hashes; generate activation links after restoring them.
- Treat database dumps and backup archives as sensitive even though passwords are hashed.

Google callback acceptance/rejection is covered with a mocked provider. A live Google tenant integration still requires real client credentials and a browser verification before relying on that method in production.
