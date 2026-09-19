# Starting a new development or maintenance session

This repository contains the product decisions, implementation map, runnable setup and operational procedures needed to continue work without a previous chat. Installation secrets and private CRM data remain outside Git.

## Establish the current state

1. Read [the root contributor context](../CLAUDE.md), [architecture](architecture.md) and the relevant guide from [the index](README.md).
2. Inspect `git status --short --branch` and `git log -5 --oneline`. Preserve existing changes. Compare remote state before pushing; `origin/main` is only as current as the last fetch.
3. Read `pyproject.toml`, `uv.lock`, `CHANGELOG.md` and `compose.yaml` for package/dependency versions and the default image. GitHub release/image state and the deployed image are separate facts; verify them when relevant.
4. For a fresh clone, follow [installation](installation.md) or [development](development.md). Do not regenerate an existing `.env`.
5. If present, read the ignored `.local/OPERATIONS.md` for private installation locations and prior verification evidence. Treat it as a reference, not proof that a service is currently running.

Before any import, migration, backup or restore, identify the actual target. From the repository root with dependencies installed, this prints configuration without credentials:

```sh
uv run python - <<'PY'
from overdrive.config import Settings
s = Settings()
for field in ("app_url", "surreal_url", "surreal_namespace", "surreal_database", "surreal_user", "storage_path", "dev_login"):
    print(f"{field}={getattr(s, field)}")
PY
```

The API endpoint, namespace/database and file directory must all refer to the intended installation. `localhost` may be a real workspace; it does not mean disposable. Do not print `.env`, tokens, password hashes or activation links while collecting diagnostics.

## Choose the right workflow

| Task | Read first | Verify |
| --- | --- | --- |
| Product/UI change | [Usage](usage.md), [template context](../overdrive/templates/CLAUDE.md) | Relevant route tests and browser behavior for interaction changes. |
| Business logic or permissions | [Architecture](architecture.md), [application context](../overdrive/CLAUDE.md) | Regression across affected UI/API/MCP paths and access boundaries. |
| Database/schema | [Database](database.md) | Fresh install and upgrade; no edits to applied migrations. |
| Login or remote MCP | [Authentication](authentication.md), [MCP](mcp.md) | Actual external origin, OAuth consent, initialization, tool listing and a read-only call. |
| Backup/import/restore | [Operations](operations.md), [imports](imports.md) | Correct target, records and file checksums; source data remains recoverable. |
| Release | [Development](development.md), [changelog](../CHANGELOG.md) | CI, both image platforms, version/latest tags and explicit deployment status. |

The tests and private operational scripts are not interchangeable. Public test fixtures use disposable namespaces; smoke scripts create CRM records or reset test passwords. A private helper under `.local/` may target a real workspace and must be reviewed before use.

## Finish with a verifiable handoff

- Describe behavior changed, checks run and remaining limits. Distinguish tested code, published image and running deployment.
- Update the relevant public guide/index and the changelog for a release; keep release pins consistent in README, Compose and installation examples.
- Preserve installation-specific paths and verification results in private storage, not public documentation. Never infer that a historical backup is current.
- Report `git status` and the commit/release when relevant. Do not publish private refs, dumps, screenshots or generated evidence.

Only installation secrets and access to the running host need to come from the operator or secret store. A fresh clone does not need the original company's records or a private migration archive to develop, test or deploy Overdrive.
