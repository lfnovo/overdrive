# Import and legacy maintenance tools

These are operator-run tools, not an ongoing synchronization service. Run commands from the repository root with installed dependencies. Configure the intended database and storage directory, take a backup, and stop application writes before applying an import or legacy conversion. Keep input files and reports in private ignored storage such as `.local/import/`.

Unlike `migrate.py` and `activate_user.py`, these tools do not enforce `--expect-ns`/`--expect-db`. Check the effective settings using [the session guide](session-guide.md) first. Dry runs can read the target database; file download mode also makes network requests and writes local files.

## Normalized CSV

`scripts/import_csv.py` accepts UTF-8 CSV with a header. It validates rows, references, owner access and file sizes before applying mutations. The configured `ADMIN_EMAIL` must identify an existing active administrator; referenced owners must already exist. Admin-owned rows can create missing business units; non-admin owners need existing membership.

| Columns | Format |
| --- | --- |
| `source_id`, `title` | Required unique external ID and deal title. Keep IDs stable across retries. |
| `value_brl`, `stage`, `outcome` | Decimal BRL amount, one of the current stage names, and `open`/`won`/`lost`. Defaults: zero, New Lead, open. |
| `loss_reason`, `expected_close` | Optional text and ISO `YYYY-MM-DD`. |
| `unit`, `owner_email` | Unit name and existing owner email; empty owner defaults to the configured administrator. |
| `organization`, `organization_source_id` | Optional organization name and stable external identity for sharing across rows. |
| `contact_name`, `contact_email`, `contact_phone`, `contact_source_id` | Optional contact details and stable external identity. |
| `file_paths` | Semicolon-separated paths relative to the CSV directory; files cannot escape that directory. |
| `link_url`, `link_title` | Optional HTTP/HTTPS material link and label. |

Legacy `markdown_path`, `pdf_path` and `gamma_url` columns are accepted for compatibility; new exports should use the generic file/link columns. If organization/contact source IDs are omitted, they default to the deal's source ID rather than deduplicating by name.

```sh
PYTHONPATH=. uv run python scripts/import_csv.py .local/import/deals.csv --report .local/import/preview.json
# After reviewing the dry-run report and backup:
PYTHONPATH=. uv run python scripts/import_csv.py .local/import/deals.csv --apply --report .local/import/applied.json
```

Application is incremental, not one transaction for the whole CSV. On error, earlier writes may already be committed. Stable source IDs and idempotency keys support retries of the same input; they do not provide an overwrite/update synchronization mode. Inspect the report before retrying, and keep file ordering stable. The normalized importer does not import tasks or the full source audit history. Tests and detailed field parsing are in `tests/test_importer.py` and `overdrive/importer.py`.

## Offline Pipedrive snapshot

`scripts/import_pipedrive.py` consumes a complete offline MCP/API snapshot plus an explicit private mapping. It is a historical migration helper with opinionated source stage IDs and custom-field mappings in `overdrive/pipedrive_import.py`; review/adapt these for a different Pipedrive account. It is not a generic raw-export importer.

The snapshot includes `extracted_at`, `deals_labeled`, `persons`, `organizations`, `stages`, `notes`, and deal-ID-keyed `participants` and `activities`. Collection values are source response envelopes with `success: true` and `data`; unfinished pagination is rejected. The mapping includes an admin `actor` record ID, `deal_units` mapping source deal IDs to target unit IDs or null, and `users` mapping source user IDs to objects with target `id`, original `name`, and optional `create` user data. Target units must exist; all owners and references are checked before applying.

```sh
PYTHONPATH=. uv run python scripts/import_pipedrive.py .local/import/snapshot.json .local/import/mapping.json
PYTHONPATH=. uv run python scripts/import_pipedrive.py .local/import/snapshot.json .local/import/mapping.json --apply
```

Reports are written beside the snapshot. New records use deterministic `*:pipedrive_*` IDs and are committed in one database transaction. Existing IDs are skipped to preserve later edits; rerunning does not synchronize source changes. The tool preserves dates, notes and tasks but cannot reconstruct missing stage history. Currency must be BRL. It makes no network calls or writes to Pipedrive.

## Pipedrive file downloads

The file helper expects `mapping.json` and `files-inventory.json` in the given directory. The inventory maps source deal IDs to file metadata containing `id`, `deal_id`, `name`, `file_size`, `add_time`, and optional description/active flag. It links uploads to already-imported `deal:pipedrive_<id>` records.

Configure `PIPEDRIVE_API_TOKEN` privately in the process environment or `.env`; optionally set `PIPEDRIVE_ORIGIN` to the account's HTTPS Pipedrive API origin.

```sh
PYTHONPATH=. uv run python scripts/import_pipedrive_files.py .local/import
PYTHONPATH=. uv run python scripts/import_pipedrive_files.py .local/import --apply
```

Without `--apply`, this downloads and verifies files but does not write CRM records. With `--apply`, it imports through the storage service using durable source-file keys and verifies the resulting checksums. It validates size, PDF signatures and UTF-8 Markdown. Its redirect allowlist is limited to the original Pipedrive host and Amazon S3 domains; credentials are never forwarded to storage hosts. Other destinations require explicit investigation, not disabling the checks. This helper is oriented toward PDF/Markdown migration, not arbitrary file-type conversion.

## Legacy data conversions

`scripts/migrate_stages.py` maps known old stage names to the current pipeline and rejects unknown stages. `scripts/migrate_materials.py` preserves old proposal content as generic files/links, enriches older attachment metadata and keeps legacy records for backup compatibility.

These commands write immediately, without an `--apply` or dry-run flag. They are not the numbered schema migrations and are not needed for a fresh installation or routine patch upgrade. Use only for a verified legacy dataset, with the app stopped, the appropriate schema already applied and a matching backup. They are designed for reruns, but operate incrementally; inspect any partial failure before continuing.
