"""One-time preservation of legacy proposals. Run with the app stopped after schema bootstrap.

Legacy records remain read-only in backups. Deterministic IDs and per-deal transactions
make reruns safe. New app operations never create proposal records.
"""

import hashlib

from .models import Attachment
from .service import Actor
from .storage import LocalStorage


async def migrate_materials(crm, settings):
    storage = LocalStorage(settings.storage_path, settings.max_upload_bytes)
    admins = await crm.db.rows("SELECT * FROM user WHERE admin = true AND active = true LIMIT 1;")
    if not admins:
        raise ValueError("An active administrator is required.")
    actor = Actor(admins[0]["id"], "migration", "generic-materials")
    report = {"files_upgraded": 0, "proposals_preserved": 0, "items_created": 0}

    async def author(target):
        events = await crm.db.rows(
            "SELECT * FROM audit_event WHERE target = $target ORDER BY created_at ASC LIMIT 1;",
            {"target": target},
        )
        return (
            {"created_by": events[0]["actor"], "creator_name": events[0]["actor_name"]}
            if events
            else {"creator_name": "Team"}
        )

    async with crm.db.lock:
        # Enrich existing binaries without changing IDs, contents, or download URLs.
        for record in await crm.db.rows("SELECT * FROM attachment;"):
            if record.get("title"):
                continue
            updated = (
                record
                | {
                    "title": record.get("name") or "File",
                    "description": "",
                    "url": "",
                    "deprecated": False,
                }
                | await author(record["id"])
            )
            await crm._commit(
                actor, [(record["id"], record, updated)], "material.migrate", audit_deal=record["deal"]
            )
            report["files_upgraded"] += 1
        for deal in await crm.db.rows("SELECT * FROM deal;"):
            proposals = await crm.db.rows(
                "SELECT * FROM proposal_version WHERE deal = $deal ORDER BY number ASC;",
                {"deal": crm._db_value({"deal": deal["id"]})["deal"]},
            )
            pending = [p for p in proposals if not p.get("materials_migrated")]
            if not pending:
                continue
            changes, written = [], []
            current = deal.get("current_proposal")
            try:
                for p in pending:
                    metadata = {
                        "deal": deal["id"],
                        "deprecated": bool(current and p["id"] != current),
                        "created_at": p["created_at"],
                        **await author(p["id"]),
                    }
                    if p.get("markdown"):
                        content = p["markdown"].encode("utf-8")
                        key = hashlib.sha256((p["id"] + ":markdown").encode()).hexdigest()[:32]
                        path = storage.path(key)
                        if path.exists():
                            if path.read_bytes() != content:
                                raise ValueError("Existing migration file differs from the original.")
                        else:
                            with path.open("xb") as stream:
                                stream.write(content)
                            written.append(path)
                        title = f"Document v{p['number']}.md"
                        record = metadata | {
                            "title": title,
                            "name": title,
                            "description": "",
                            "url": "",
                            "mime": "text/markdown",
                            "storage_key": key,
                            "size": len(content),
                            "checksum": hashlib.sha256(content).hexdigest(),
                        }
                        changes.append(("attachment:" + key, None, record))
                    if p.get("gamma_url"):
                        key = hashlib.sha256((p["id"] + ":link").encode()).hexdigest()[:32]
                        data = Attachment(
                            deal=deal["id"], title=f"Document link v{p['number']}", url=p["gamma_url"]
                        ).model_dump()
                        changes.append(("attachment:" + key, None, data | metadata))
                    changes.append((p["id"], p, p | {"materials_migrated": True}))
                # A shared binary is valid if any legacy current proposal references it.
                for file_id in {p["attachment"] for p in pending if p.get("attachment")}:
                    f = await crm.db.get(file_id)
                    if not f or f["deal"] != deal["id"]:
                        raise ValueError("Invalid legacy file reference.")
                    stale = bool(
                        current
                        and not any(p.get("attachment") == file_id and p["id"] == current for p in proposals)
                    )
                    changes.append((f["id"], f, f | {"deprecated": stale}))
                updated_deal = {k: v for k, v in deal.items() if k != "current_proposal"}
                changes.append((deal["id"], deal, updated_deal))
                # First result must be an active resource, even for an empty legacy proposal.
                changes.sort(key=lambda c: c[0].startswith("proposal_version:"))
                await crm._commit(actor, changes, "material.migrate", audit_deal=deal["id"])
                report["proposals_preserved"] += len(pending)
                report["items_created"] += sum(before is None for _, before, _ in changes)
            except Exception:
                for path in written:
                    path.unlink(missing_ok=True)
                raise
    return report
