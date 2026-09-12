"""Portable local backup of commercial records and immutable attachment bytes.

OAuth grants/sessions are deliberately excluded: restore requires reconnecting clients.
"""

import hashlib
import json
import os
import secrets
import zipfile
from pathlib import Path

from .database import ensure_record_id
from .service import CRM, now
from .storage import LocalStorage

TABLES = [
    "credential",
    "unit",
    "source",
    "user",
    "organization",
    "contact",
    "deal",
    "deal_contact",
    "task",
    "note",
    "attachment",
    "proposal_version",
    "audit_event",
    "idempotency_request",
]


async def export_backup(db, settings, output: Path):
    if output.exists():
        raise ValueError("The destination already exists. Choose another name.")
    fields = ", ".join(f'"{t}": (SELECT * FROM {t})' for t in TABLES)
    results = await db.query("BEGIN; RETURN {" + fields + "}; COMMIT;")
    records = next(r for r in results if isinstance(r, dict) and "user" in r)
    for credential in records["credential"]:
        credential["activation_hash"] = ""
        credential["activation_expires"] = 0
    storage = LocalStorage(settings.storage_path, settings.max_upload_bytes)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(output.suffix + ".tmp")
    try:
        with zipfile.ZipFile(temp, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "manifest.json",
                json.dumps({"format": 2, "created_at": now(), "records": records}, ensure_ascii=False),
            )
            for row in records["attachment"]:
                if not row.get("storage_key"):
                    continue
                content = storage.path(row["storage_key"]).read_bytes()
                if len(content) != row["size"] or hashlib.sha256(content).hexdigest() != row["checksum"]:
                    raise ValueError("Missing or corrupted file: " + row["id"])
                archive.writestr("files/" + row["storage_key"], content)
        temp.chmod(0o600)
        os.replace(temp, output)
    finally:
        temp.unlink(missing_ok=True)
    return {t: len(rows) for t, rows in records.items()}


async def restore_backup(db, settings, source: Path):
    # Fresh, pre-bootstrapped database only: never merge or overwrite existing data.
    for table in TABLES + ["auth_record"]:
        if await db.rows(f"SELECT VALUE id FROM {table} LIMIT 1;"):
            raise ValueError("Restore requires an empty, migrated database with the app stopped.")
    storage = LocalStorage(settings.storage_path, settings.max_upload_bytes)
    crm = CRM(db)
    written = []
    try:
        with zipfile.ZipFile(source) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            if (manifest.get("format"), frozenset(manifest["records"])) not in {
                (2, frozenset(TABLES)),
                (1, frozenset(TABLES) - {"credential"}),
                (1, frozenset(TABLES) - {"credential", "source"}),
            }:
                raise ValueError("Invalid backup format.")
            records = manifest["records"]
            records.setdefault("source", [])
            records.setdefault("credential", [])
            for credential in records["credential"]:
                credential.update(epoch=secrets.token_hex(16), activation_hash="", activation_expires=0)
            for row in records["attachment"]:
                if not row.get("storage_key"):
                    continue
                content = archive.read("files/" + row["storage_key"])
                if len(content) != row["size"] or hashlib.sha256(content).hexdigest() != row["checksum"]:
                    raise ValueError("Checksum mismatch: " + row["id"])
                path = storage.path(row["storage_key"])
                if path.exists():
                    raise ValueError("The destination contains existing files. Use an empty directory.")
                with path.open("xb") as stream:
                    stream.write(content)
                written.append(path)
            sql, params = ["BEGIN;"], {}
            index = 0
            for table in TABLES:
                for row in records[table]:
                    params[f"id{index}"] = ensure_record_id(row["id"], table)
                    params[f"data{index}"] = crm._db_value({k: v for k, v in row.items() if k != "id"})
                    if table == "credential":
                        params[f"data{index}"]["subject"] = ensure_record_id(row["subject"], "user")
                    sql.append(f"CREATE $id{index} CONTENT $data{index};")
                    index += 1
            sql.append("COMMIT;")
            await db.query("\n".join(sql), params)
        return {t: len(rows) for t, rows in records.items()}
    except Exception:
        for path in written:
            path.unlink(missing_ok=True)
        raise
