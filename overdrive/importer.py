"""Import a normalized CSV export. Validate all rows before applying any mutation."""

import csv
from decimal import Decimal
from pathlib import Path

from starlette.datastructures import UploadFile

from .database import DatabaseError
from .models import Attachment, Contact, Deal, Organization, Unit
from .service import Actor, DomainError
from .stages import LEGACY_STAGES
from .storage import LocalStorage


def read_export(path: Path):
    rows = []
    seen = set()
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for line, row in enumerate(csv.DictReader(stream), 2):
            source = row.get("source_id", "").strip()
            if not source or source in seen or len(source) > 100:
                raise ValueError(f"Row {line}: source_id is missing, duplicated or too long.")
            seen.add(source)
            amount = Decimal(row.get("value_brl") or "0") * 100
            if not amount.is_finite() or amount != amount.to_integral_value():
                raise ValueError(f"Row {line}: invalid amount.")
            data = Deal(
                title=row.get("title", ""),
                value_cents=int(amount),
                stage=LEGACY_STAGES.get(row.get("stage"), row.get("stage") or "New Lead"),
                outcome=row.get("outcome") or "open",
                loss_reason=row.get("loss_reason") or "",
                expected_close=row.get("expected_close") or None,
            ).model_dump(mode="json")
            files = {}
            paths = [p.strip() for p in row.get("file_paths", "").split(";") if p.strip()]
            paths += [row[field] for field in ["markdown_path", "pdf_path"] if row.get(field)]
            for field in dict.fromkeys(paths):
                if field:
                    file = (path.parent / field).resolve()
                    if not file.is_relative_to(path.parent.resolve()) or not file.is_file():
                        raise ValueError(f"Row {line}: file is missing or outside the export directory.")
                    files[field] = file
            link_url = row.get("link_url") or row.get("gamma_url") or ""
            link_title = row.get("link_title") or "Imported link"
            if link_url:
                Attachment(deal="deal:preview", title=link_title, url=link_url)
            if row.get("unit"):
                Unit(name=row["unit"])
            if row.get("organization"):
                Organization(name=row["organization"])
            if row.get("contact_name"):
                Contact(
                    name=row["contact_name"],
                    email=row.get("contact_email") or "",
                    phone=row.get("contact_phone") or "",
                )
            for field in ["organization_source_id", "contact_source_id"]:
                if len(row.get(field) or "") > 100:
                    raise ValueError(f"Row {line}: external identifier is too long.")
            rows.append(
                {
                    "source": source,
                    "line": line,
                    "data": data,
                    "unit": row.get("unit", "").strip(),
                    "owner_email": row.get("owner_email", "").strip().lower(),
                    "organization": row.get("organization", "").strip(),
                    "contact_name": row.get("contact_name", "").strip(),
                    "contact_email": row.get("contact_email", "").strip(),
                    "contact_phone": row.get("contact_phone", "").strip(),
                    "organization_source": row.get("organization_source_id") or source,
                    "contact_source": row.get("contact_source_id") or source,
                    "link_url": link_url,
                    "link_title": link_title,
                    "files": files,
                }
            )
    return rows


async def import_export(crm, settings, path: Path, apply=False):
    rows = read_export(path)
    admins = await crm.db.rows(
        "SELECT * FROM user WHERE email = $email AND admin = true AND active = true;",
        {"email": settings.admin_email.lower()},
    )
    if not admins:
        raise ValueError("The initial administrator must exist. Start the app once.")
    actor = Actor(admins[0]["id"], "import", "normalized-csv")
    units = {u["name"]: u for u in await crm.listing(actor, "unit", limit=200)}
    users = {u["email"]: u for u in await crm.listing(actor, "user", limit=200)}
    for r in rows:
        owner = users.get(r["owner_email"] or settings.admin_email.lower())
        if not owner or not owner["active"]:
            raise ValueError(f"Row {r['line']}: owner is missing or inactive.")
        if not owner["admin"] and (r["unit"] not in units or units[r["unit"]]["id"] not in owner["units"]):
            raise ValueError(f"Row {r['line']}: owner does not belong to the business unit.")
        r["owner"] = owner["id"]
        for f in r["files"].values():
            if not 0 < f.stat().st_size <= settings.max_upload_bytes:
                raise ValueError(f"Row {r['line']}: file exceeds the size limit.")
    report = {
        "mode": "apply" if apply else "dry-run",
        "rows": len(rows),
        "value_cents": sum(r["data"]["value_cents"] for r in rows),
        "files": sum(len(r["files"]) for r in rows),
        "deals": [],
        "errors": [],
    }
    if not apply:
        return report
    storage = LocalStorage(settings.storage_path, settings.max_upload_bytes)
    for r in rows:
        key = "import:" + r["source"]
        try:
            unit = None
            if r["unit"]:
                if r["unit"] not in units:
                    units[r["unit"]] = await crm.save(
                        actor, "unit", {"name": r["unit"]}, key="import-unit:" + r["unit"]
                    )
                unit = units[r["unit"]]["id"]
            organization = None
            if r["organization"]:
                organization = await crm.save(
                    actor,
                    "organization",
                    {"name": r["organization"]},
                    key="import:organization:" + r["organization_source"],
                )
            deal = await crm.save(
                actor,
                "deal",
                r["data"]
                | {
                    "unit": unit,
                    "owner": r["owner"],
                    "organization": organization["id"] if organization else None,
                },
                key=key + ":deal",
            )
            if r["contact_name"]:
                contact = await crm.save(
                    actor,
                    "contact",
                    {
                        "name": r["contact_name"],
                        "email": r["contact_email"],
                        "phone": r["contact_phone"],
                        "organization": organization["id"] if organization else None,
                    },
                    key="import:contact:" + r["contact_source"],
                )
                await crm.link(actor, deal["id"], contact["id"])
            for index, file in enumerate(r["files"].values()):
                with file.open("rb") as stream:
                    await storage.save(
                        crm,
                        actor,
                        deal["id"],
                        UploadFile(filename=file.name, file=stream),
                        key + ":file:" + str(index),
                    )
            if r["link_url"]:
                await crm.save(
                    actor,
                    "attachment",
                    {"deal": deal["id"], "title": r["link_title"], "url": r["link_url"]},
                    key=key + ":link",
                )
            report["deals"].append({"source_id": r["source"], "id": deal["id"]})
        except (DomainError, DatabaseError, OSError, ValueError) as error:
            report["errors"].append({"source_id": r["source"], "error": str(error)})
            break
    return report
