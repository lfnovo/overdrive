"""Offline, admin-only import of a complete Pipedrive MCP snapshot.

No Pipedrive credentials or network writes. Deterministic source IDs and one
transaction make retry safe; existing imported records are never overwritten.
Keep snapshots and mapping files under .local/, outside distributable code.
"""

import re
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from html.parser import HTMLParser

from .models import MODELS
from .service import Actor, DomainError

STAGE_MAP = {
    6: "Briefing Ready",
    7: "Briefing Ready",
    8: "Briefing Ready",
    9: "Proposal Ready",
    10: "Proposal Sent",
    11: "Negotiation",
    20: "Contract",
}
FIELD_NAMES = {
    "b46616b85c732237e4402014742c3a9262ff245a": "Original service type",
    "ca582902a5d58d25b01fd098c717b49ae44b7064": "Original material link",
    "13aaee2636fe5113eb897a15a2346090c8525f80": "Original current filename",
    "ae155c7242584202b7e975b2415432d11fb9ac19": "First sent date",
    "579dfcdd411637698432079ee707f9d8a0c070e9": "Current material date",
    "132164804b1a8382d55854c99432a3e827394ecb": "Investment scenarios",
    "ce5bd20b3ef0dbc3ad0f634b2e7740872a044267": "Legacy ID",
}


class HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.links = [], []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.skip += 1
        if self.skip:
            return
        if tag in {"p", "div", "br", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("\n- ")
        if tag == "a":
            url = dict(attrs).get("href", "")
            self.links.append(url if url.startswith(("https://", "http://", "mailto:")) else "")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag in {"p", "div", "tr", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")
        if tag == "a" and self.links:
            url = self.links.pop()
            if url:
                self.parts.append(f" ({url})")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def plain_html(value):
    parser = HTMLText()
    parser.feed(value or "")
    return re.sub(r"\n{3,}", "\n\n", "".join(parser.parts)).strip()


def source_id(table, id):
    key = str(id).replace("-", "_")
    if not re.fullmatch(r"[A-Za-z0-9_]+", key):
        raise ValueError("Invalid source ID")
    return f"{table}:pipedrive_{key}"


def timestamp(value):
    # Pipedrive v1 dates are UTC, while v2 includes the timezone explicitly.
    dt = datetime.fromisoformat(value)
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).astimezone(UTC).isoformat()


def money(value, currency):
    if currency != "BRL":
        raise ValueError(f"Unsupported currency: {currency}")
    cents = Decimal(str(value)) * 100
    if cents != cents.to_integral_value():
        raise ValueError("Fractional cents in source")
    return int(cents)


def complete_data(response):
    if response.get("success") is not True:
        raise ValueError("Unsuccessful source response")
    extra = response.get("additional_data") or {}
    if extra.get("next_cursor") or extra.get("pagination", {}).get("more_items_in_collection"):
        raise ValueError("Incomplete source pagination")
    return response.get("data") or []


def build_plan(snapshot, mapping):
    records = []
    users = {int(k): v for k, v in mapping["users"].items()}
    deals = complete_data(snapshot["deals_labeled"])
    persons = {p["id"]: p for p in complete_data(snapshot["persons"])}
    orgs = {o["id"]: o for o in complete_data(snapshot["organizations"])}
    stages = {s["id"]: s["name"] for s in complete_data(snapshot["stages"])}
    linked_people, linked_orgs, source_deals = set(), set(), {}

    def add(table, key, data, date, **extra):
        parsed = MODELS[table].model_validate(data).model_dump(mode="json")
        record = {**parsed, **extra, "created_at": timestamp(date)}
        records.append((source_id(table, key), record))

    for source, user in users.items():
        if user.get("create"):
            add("user", source, user["create"], snapshot["extracted_at"])
            if user["id"] != source_id("user", source):
                raise ValueError("New user ID must preserve its source ID")

    for deal in deals:
        did = deal["id"]
        unit = mapping["deal_units"][str(did)]
        contacts = complete_data(snapshot["participants"][str(did)])
        pids = {p["id"] for p in contacts}
        if deal.get("person_id"):
            pids.add(deal["person_id"])
        linked_people.update(pids)
        if deal.get("org_id"):
            linked_orgs.add(deal["org_id"])
        target = source_id("deal", did)
        source_deals[did] = target
        add(
            "deal",
            did,
            {
                "title": deal["title"],
                "unit": unit,
                "organization": source_id("organization", deal["org_id"]) if deal.get("org_id") else None,
                "owner": users[deal["owner_id"]]["id"],
                "stage": STAGE_MAP[deal["stage_id"]],
                "outcome": deal["status"],
                "value_cents": money(deal["value"], deal["currency"]),
                "expected_close": deal.get("expected_close_date"),
                "loss_reason": deal.get("lost_reason") or "",
                "archived": deal.get("is_archived", False),
            },
            deal["add_time"],
        )
        for pid in sorted(pids):
            records.append(
                (
                    source_id("deal_contact", f"{did}_{pid}"),
                    {
                        "deal": target,
                        "contact": source_id("contact", pid),
                        "created_at": timestamp(deal["add_time"]),
                    },
                )
            )
        details = [
            f"Imported from Pipedrive · deal #{did}",
            f"Original stage: {stages[deal['stage_id']]}",
            f"Original owner: {users[deal['owner_id']]['name']}",
        ]
        for field in ("add_time", "update_time", "won_time", "lost_time", "close_time", "stage_change_time"):
            if deal.get(field):
                details.append(f"{field}: {deal[field]}")
        for key, value in (deal.get("custom_fields") or {}).items():
            if value is not None and value != "":
                if isinstance(value, dict):
                    value = value.get("label", str(value))
                details.append(f"{FIELD_NAMES.get(key, key)}: {value}")
                if isinstance(value, str) and value.startswith(("https://", "http://")):
                    add(
                        "attachment",
                        f"link_{did}_{key}",
                        {
                            "deal": target,
                            "title": "Imported material link",
                            "url": value,
                            "description": f"Pipedrive deal #{did} · {FIELD_NAMES.get(key, key)}",
                        },
                        deal["add_time"],
                        created_by=users[deal["owner_id"]]["id"],
                        creator_name=users[deal["owner_id"]]["name"],
                    )
        for pid in sorted(pids):
            person = persons[pid]
            details.append(f"Contact: {person['name']} (Pipedrive #{pid})")
            for field in ("emails", "phones"):
                for entry in person.get(field) or []:
                    details.append(f"  {field}: {entry['value']} ({entry.get('label', '')})")
        add(
            "note",
            f"metadata_{did}",
            {"deal": target, "content": "\n\n".join(details)},
            snapshot["extracted_at"],
            actor=mapping["actor"],
        )

    # Leads remain in the source snapshot until explicitly selected for import.
    for pid in sorted(linked_people):
        person = persons[pid]
        oid = person.get("org_id")
        if oid:
            linked_orgs.add(oid)

        def primary(field, person=person):
            entries = person.get(field) or []
            return next(
                (e["value"] for e in entries if e.get("primary")), entries[0]["value"] if entries else ""
            )

        add(
            "contact",
            pid,
            {
                "name": person["name"],
                "email": primary("emails"),
                "phone": primary("phones"),
                "organization": source_id("organization", oid) if oid else None,
            },
            person["add_time"],
        )
    for oid in sorted(linked_orgs):
        org = orgs[oid]
        add("organization", oid, {"name": org["name"], "domain": org.get("website") or ""}, org["add_time"])

    for note in complete_data(snapshot["notes"]):
        if not note.get("active_flag", True) or note.get("deal_id") not in source_deals:
            continue
        author = note.get("user") or {}
        content = (
            f"{author.get('name', 'Pipedrive')} · {note['add_time']} UTC · Pipedrive note #{note['id']}\n\n"
        )
        content += plain_html(note["content"])
        add(
            "note",
            note["id"],
            {"deal": source_deals[note["deal_id"]], "content": content},
            note["add_time"],
            actor=users[note["user_id"]]["id"],
        )

    for did, target in source_deals.items():
        activities = complete_data(snapshot["activities"][str(did)])
        pending = sorted(
            (a for a in activities if not a["done"] and not a.get("is_deleted")),
            key=lambda a: (a.get("due_date") or "9999", a["id"]),
        )
        primary_id = pending[0]["id"] if pending else None
        for task in activities:
            if task.get("is_deleted"):
                continue
            description = [
                plain_html(task.get("note")),
                plain_html(task.get("public_description")),
                f"Pipedrive activity #{task['id']} · {task['type']}",
            ]
            for field in (
                "due_time",
                "duration",
                "marked_as_done_time",
                "location",
                "conference_meeting_url",
            ):
                if task.get(field):
                    description.append(f"{field}: {task[field]}")
            add(
                "task",
                task["id"],
                {
                    "deal": target,
                    "title": task["subject"],
                    "description": "\n\n".join(filter(None, description)),
                    "owner": users[task["owner_id"]]["id"],
                    "due": task.get("due_date") or None,
                    "done": task["done"],
                    "primary": task["id"] == primary_id,
                },
                task["add_time"],
            )
    if len({id for id, _ in records}) != len(records):
        raise ValueError("Duplicate source IDs")
    return records


async def apply_plan(crm, actor_id, records, *, apply=False):
    actor = Actor(actor_id, "import", "Pipedrive migration")
    if not (await crm.user(actor))["admin"]:
        raise DomainError("Migration requires an administrator.", 403)
    async with crm.db.lock:
        existing = {}
        for table in {id.split(":")[0] for id, _ in records} | {"unit", "user", "deal"}:
            existing.update({r["id"]: r for r in await crm.db.rows(f"SELECT * FROM {table};")})
        pending = [(id, record) for id, record in records if id not in existing]
        merged = dict(records) | existing
        for id, record in pending:
            for field in ("deal", "contact", "organization", "unit", "owner", "actor", "created_by"):
                if record.get(field) and record[field] not in merged:
                    raise ValueError(f"Missing reference {id}.{field}")
            for unit in record.get("units", []):
                if unit not in merged or merged[unit].get("archived"):
                    raise ValueError("Invalid user unit")
            if id.startswith(("deal:", "task:")) and record.get("owner"):
                owner = merged[record["owner"]]
                unit = record.get("unit") if id.startswith("deal:") else merged[record["deal"]].get("unit")
                if not owner["active"] or not (owner["admin"] or unit in owner["units"]):
                    raise ValueError(f"Owner cannot access {id}")
        counts = dict(Counter(id.split(":")[0] for id, _ in pending))
        if apply and pending:
            await crm._commit(actor, [(id, None, data) for id, data in pending], "pipedrive.import")
        return {
            "applied": apply,
            "new": counts,
            "skipped_existing": len(records) - len(pending),
            "planned": len(records),
            "ids": [id for id, _ in records],
        }
