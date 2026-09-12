# MCP for agents

Overdrive exposes `/mcp` using Streamable HTTP. Use a client that supports remote MCP with OAuth discovery, dynamic client registration, browser consent and PKCE S256. Supply the external URL configured in `APP_URL`, for example `https://crm.example.com/mcp`.

The agent's user signs in with a local password or Google and approves the connection in the browser. Tokens identify that user; membership and account status are checked again during operations. Administrative tools require an administrator. Revoke connections under Settings. The agent does not receive a database password.

## Tools

| Tools | Use |
| --- | --- |
| `whoami` | Identity, units and privileges. |
| `list_records`, `get_record`, `record_history` | Search accessible records and inspect current versions/history. |
| `create_deal`, `update_record` | Create, edit, archive and change deal outcomes. |
| `create_contact`, `create_organization`, `link_contact` | Manage deal relationships. |
| `create_task`, `my_tasks` | Assign next steps and retrieve personal work. |
| `add_note` | Append dated Markdown context. |
| `add_link`, `attachment_transfer`, `delete_attachment` | Store and retrieve materials. |
| `pipeline_analytics` | Metrics restricted to the user's accessible deals. |
| `transfer_deal`, `admin_save` | Administrative transfers and configuration. |

## Editing safely

Read a record first. Pass its `version` as `expected_version`, send only the fields you intend to change, and generate an `idempotency_key` for that operation. Reuse that key for retries of the **same** payload. If the record changed, read it again and reconcile the newer state rather than overwriting it blindly.

A living summary is updated with `update_record(record_id=..., expected_version=..., changes={"deal_so_far": "...Markdown..."}, idempotency_key=...)`. Preserve useful decisions and open questions. Add dated observations with `add_note`. Overdrive stores these outputs; it does not run an agent or generate summaries automatically.

Amounts use integer `value_cents` in BRL. Dates use ISO `YYYY-MM-DD`. Discover source IDs with `list_records(entity="source")`; set a source ID or null on the deal. Stage, outcome and archived are separate fields.

## Files

`attachment_transfer` returns a short-lived, deal-scoped URL. For uploads, send multipart form data with a `file` field and optional `title`/`description`, plus the required `Idempotency-Key`. The agent does not need to copy its bearer token into the file URL. Upload tickets are single-use, expire after five minutes, and retain revocation/access checks.

Treat temporary file URLs as credentials and keep them out of public logs. Mark previous material with `deprecated=true` when it is outdated; upload order does not imply version relationships.

## Compatibility

Clients must implement the OAuth flow; an HTTPS endpoint alone is insufficient. Local simulated login exists for testing, but a real client should be tested against the final HTTPS origin and enabled sign-in methods. The automated suite covers registration, consent, PKCE, token rotation, revocation and MCP calls with development and local-password identities.
