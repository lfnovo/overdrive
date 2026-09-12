# Documentation

- [Authentication](authentication.md): local passwords, activation links, recovery and Google.
- [Installation](installation.md): Compose, environment variables, Google identity and HTTPS.
- [Usage](usage.md): pipeline, business units, contacts, tasks, materials and analytics.
- [MCP](mcp.md): agent authorization, tools and safe updates.
- [Database](database.md): connections, schema migrations and adoption of an existing database.
- [Operations](operations.md): persistence, backup, restore and upgrades.
- [Development](development.md): local development, automated checks and release workflow.

## Current boundaries

Overdrive 0.1 is an early, single-company CRM. The six pipeline stages are fixed, amounts are BRL, and dates use America/Sao_Paulo. Authentication supports local passwords and optional Google for humans, with OAuth tokens for MCP clients. Development login is an explicit local-only evaluation mode.

Uploads are stored in a filesystem volume, not SurrealDB BLOB storage. There is no built-in agent, email sync, multi-company hosting or automatic proposal generation. Deal deletion is represented by archiving; attachment deletion removes its stored bytes. Analytics use observed history and do not fabricate missing stage dates for imports.

Production installation requires HTTPS, persistent storage and a backup process. Google credentials are required only when enabling Google sign-in. Real Google-provider validation remains deployment-specific; automated tests exercise authorization and the MCP OAuth protocol with local-password and simulated development identities.
