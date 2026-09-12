"""The single authorization and mutation boundary used by web, API and MCP."""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import ValidationError

from .database import DatabaseError, ensure_record_id
from .models import MODELS, REFERENCES


def now():
    return datetime.now(UTC).isoformat()


def new_id(table):
    return f"{table}:{uuid.uuid4().hex}"


class DomainError(Exception):
    def __init__(self, message, status=400, code="validation"):
        super().__init__(message)
        self.message, self.status, self.code = message, status, code


@dataclass
class Actor:
    id: str
    channel: str = "web"
    client: str = ""


class CRM:
    def __init__(self, db):
        self.db = db

    async def user(self, actor):
        try:
            user = await self.db.get(str(ensure_record_id(actor.id, "user")))
        except ValueError:
            user = None
        if not user or not user["active"]:
            raise DomainError("Session expired or access revoked.", 401, "unauthorized")
        return user

    def _predicate(self, table, user):
        if user["admin"]:
            return "true"
        visible = "(SELECT VALUE id FROM deal WHERE unit IN $units)"
        contacts = f"(SELECT VALUE contact FROM deal_contact WHERE deal IN {visible})"
        return {
            "deal": "unit IN $units",
            "unit": "id IN $units",
            "source": "true",
            "contact": f"id IN {contacts}",
            "organization": f"(id IN (SELECT VALUE organization FROM deal WHERE unit IN $units) OR id IN (SELECT VALUE organization FROM contact WHERE id IN {contacts}))",
            "task": f"deal IN {visible}",
            "note": f"deal IN {visible}",
            "attachment": f"deal IN {visible}",
            "audit_event": f"deal IN {visible}",
            "deal_contact": f"deal IN {visible}",
            "user": "(id = $actor OR (admin = true AND active = true) OR array::len(array::intersect(units, $units)) > 0)",
        }.get(table, "false")

    async def listing(
        self,
        actor,
        table,
        *,
        q="",
        unit="",
        owner="",
        deal="",
        archived=False,
        outcome="",
        mine=False,
        done=None,
        deprecated=None,
        offset=0,
        limit=100,
    ):
        if table not in {*MODELS, "attachment", "audit_event", "deal_contact"}:
            raise DomainError("Resource not found.", 404, "not_found")
        user = await self.user(actor)
        params = {
            "actor": ensure_record_id(actor.id, "user"),
            "units": [ensure_record_id(x, "unit") for x in user.get("units", [])],
            "limit": max(1, min(int(limit), 200)),
            "offset": max(0, int(offset)),
        }
        clauses = [self._predicate(table, user)]
        if table in {"deal", "unit", "organization", "contact", "source"} and archived is not None:
            params["archived"] = archived
            clauses.append("archived = $archived")
        if table == "attachment" and deprecated is not None:
            params["deprecated"] = deprecated
            clauses.append("deprecated = $deprecated")
        if q:
            params["q"] = q.lower()[:200]
            field = "title" if table in {"deal", "task", "attachment"} else "name"
            if table in {"deal", "task", "contact", "organization", "unit", "user", "attachment", "source"}:
                clauses.append(f"string::contains(string::lowercase({field}), $q)")
        if unit and table in {"deal", "task"}:
            path = "unit" if table == "deal" else "deal.unit"
            if unit == "none":
                clauses.append(f"{path} = NONE" if user["admin"] else "false")
            else:
                params["unit"] = ensure_record_id(unit, "unit")
                clauses.append(f"{path} = $unit")
        if owner and table in {"deal", "task"}:
            params["owner"] = ensure_record_id(owner, "user")
            clauses.append("owner = $owner")
        if outcome and table == "deal":
            params["outcome"] = outcome
            clauses.append("outcome = $outcome")
        if deal and table in {
            "task",
            "note",
            "attachment",
            "audit_event",
            "deal_contact",
        }:
            await self.get(actor, deal, "deal")
            params["deal"] = ensure_record_id(deal, "deal")
            clauses.append("deal = $deal")
        if mine and table == "task":
            clauses.append("owner = $actor")
        if table == "task" and done is not None:
            params["done"] = done
            clauses.append("done = $done")
        sql = f"SELECT * FROM {table} WHERE " + " AND ".join(f"({c})" for c in clauses)
        rows = await self.db.rows(sql + " ORDER BY created_at DESC LIMIT $limit START $offset;", params)
        if table == "user":
            rows = [{k: v for k, v in r.items() if user["admin"] or k in {"id", "name"}} for r in rows]
        return rows

    async def get(self, actor, id, table=None):
        try:
            rid = ensure_record_id(id, table)
        except ValueError:
            raise DomainError("Resource not found.", 404, "not_found")
        name = str(rid).split(":")[0]
        if name not in {*MODELS, "attachment", "audit_event", "deal_contact"}:
            raise DomainError("Resource not found.", 404, "not_found")
        user = await self.user(actor)
        rows = await self.db.rows(
            f"SELECT * FROM $id WHERE {self._predicate(name, user)};",
            {
                "id": rid,
                "actor": ensure_record_id(actor.id),
                "units": [ensure_record_id(x) for x in user.get("units", [])],
            },
        )
        if not rows:
            raise DomainError("Resource not found.", 404, "not_found")
        if name == "user" and not user["admin"] and id != actor.id:
            return {k: v for k, v in rows[0].items() if k in {"id", "name"}}
        return rows[0]

    def _db_value(self, record):
        out = {}
        for key, value in record.items():
            if value is None:
                continue
            if key in REFERENCES:
                value = ensure_record_id(value, REFERENCES[key])
            elif key in {"actor", "contact"}:
                value = ensure_record_id(value, "user" if key == "actor" else "contact")
            elif key == "units":
                value = [ensure_record_id(v, "unit") for v in value]
            out[key] = value
        return out

    async def _commit(self, actor, changes, action, *, audit_deal=None, key="", payload=None):
        """Caller holds write lock. Record CAS, audit and dedup record commit together."""
        user = await self.user(actor)
        sql, params = ["BEGIN TRANSACTION;"], {}
        first_result = None
        for index, (id, before, after) in enumerate(changes):
            params[f"id{index}"] = ensure_record_id(id)
            deleting = after is None
            after = {
                **(after or {}),
                "version": (before or {}).get("version", 0) + 1,
                "created_at": (before or after).get("created_at", now()),
                "updated_at": now(),
            }
            after.pop("id", None)
            # Deals created before the living summary schema have no field yet.
            # CONTENT updates must supply it; schema DEFAULT does not backfill them.
            if id.startswith("deal:") and not deleting:
                after.setdefault("deal_so_far", "")
            if first_result is None:
                first_result = (
                    {
                        "id": id,
                        "deal": before["deal"],
                        "deleted": True,
                        "storage_key": before.get("storage_key"),
                    }
                    if deleting
                    else {"id": id, **after}
                )
            params[f"data{index}"] = self._db_value(after)
            if deleting:
                params[f"version{index}"] = before["version"]
                sql.append(
                    f"LET $changed{index} = DELETE $id{index} WHERE version = $version{index} RETURN BEFORE;"
                )
                sql.append(f'IF array::len($changed{index}) != 1 {{ THROW "version_conflict"; }};')
            elif before:
                params[f"version{index}"] = before["version"]
                sql.append(
                    f"LET $changed{index} = UPDATE $id{index} CONTENT $data{index} WHERE version = $version{index};"
                )
                sql.append(f'IF array::len($changed{index}) != 1 {{ THROW "version_conflict"; }};')
            else:
                sql.append(f"CREATE $id{index} CONTENT $data{index};")
            event_id = new_id("audit_event")
            event = {
                "version": 1,
                "created_at": now(),
                "updated_at": now(),
                "target": id,
                "deal": audit_deal or (id if id.startswith("deal:") else after.get("deal")),
                "action": action,
                "actor": actor.id,
                "actor_name": user["name"],
                "channel": actor.channel,
                "client": actor.client,
                "changes": {"before": before or {}, "after": {} if deleting else after},
            }
            params[f"audit{index}"] = self._db_value(event)
            params[f"event{index}"] = ensure_record_id(event_id)
            sql.append(f"CREATE $event{index} CONTENT $audit{index};")
        if key:
            if len(key) > 200:
                raise DomainError("Idempotency key is too long.")
            dedup = {
                "version": 1,
                "created_at": now(),
                "updated_at": now(),
                "actor": actor.id,
                "operation": action,
                "request_key": key,
                "payload_hash": self._hash(payload),
                "target": changes[0][0],
                "response": first_result,
            }
            params["dedup"] = self._db_value(dedup)
            sql.append("CREATE idempotency_request CONTENT $dedup;")
        sql.append("COMMIT TRANSACTION;")
        try:
            await self.db.query("\n".join(sql), params)
        except DatabaseError as exc:
            if (
                "version_conflict" in str(exc)
                or "already contains" in str(exc)
                or "transaction conflict" in str(exc).lower()
            ):
                raise DomainError(
                    "This record changed. Reload and reapply your change.", 409, "conflict"
                ) from exc
            raise
        return first_result if changes[0][2] is None else await self.get(actor, changes[0][0])

    def _hash(self, payload):
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    async def _replay(self, actor, operation, key, payload):
        if not key:
            return None
        rows = await self.db.rows(
            "SELECT * FROM idempotency_request WHERE actor = $actor AND operation = $op AND request_key = $key;",
            {"actor": ensure_record_id(actor.id), "op": operation, "key": key},
        )
        if not rows:
            return None
        if rows[0]["payload_hash"] != self._hash(payload):
            raise DomainError(
                "This key was already used with different content.", 409, "idempotency_conflict"
            )
        if operation == "attachment.delete":
            response = rows[0]["response"]
            await self.get(actor, response["deal"], "deal")
            return response
        current = await self.get(actor, rows[0]["target"])
        return rows[0].get("response") or current

    async def _owner(self, id, unit):
        if not id:
            return
        owner = await self.db.get(str(ensure_record_id(id, "user")))
        if not owner or not owner["active"] or not (owner["admin"] or (unit and unit in owner["units"])):
            raise DomainError("The owner must have access to this deal.")

    async def save(self, actor, table, data, *, id=None, version=None, context_deal=None, key=""):
        if table not in MODELS:
            raise DomainError("Invalid operation.")
        async with self.db.lock:
            user = await self.user(actor)
            action = f"{table}.update" if id else f"{table}.create"
            payload = {"data": data, "id": id, "version": version, "context_deal": context_deal}
            replay = await self._replay(actor, action, key, payload)
            if replay:
                return replay
            before = await self.get(actor, id, table) if id else None
            if before and (version is None or before["version"] != version):
                raise DomainError("This record changed. Reload before saving.", 409, "conflict")
            if table in {"user", "unit", "source"} and not user["admin"]:
                raise DomainError("Only administrators can do this.", 403, "forbidden")
            fields = MODELS[table].model_fields
            merged = {k: v for k, v in (before or {}).items() if k in fields} | data
            try:
                parsed = MODELS[table].model_validate(merged).model_dump(mode="json")
            except ValidationError as exc:
                raise DomainError(
                    "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
                ) from exc
            if before and table in {"task", "note", "attachment"} and parsed["deal"] != before["deal"]:
                raise DomainError("The linked deal cannot be changed.")
            deal = None
            if table == "deal":
                for field in ("deal_so_far_updated_at", "deal_so_far_author"):
                    if before and before.get(field):
                        parsed[field] = before[field]
                if parsed["deal_so_far"] != (before or {}).get("deal_so_far", ""):
                    parsed.update(deal_so_far_updated_at=now(), deal_so_far_author=user["name"])
                if not user["admin"] and (not parsed["unit"] or parsed["unit"] not in user.get("units", [])):
                    raise DomainError("Select a business unit you can access.", 403, "forbidden")
                if before and parsed["unit"] != before.get("unit"):
                    raise DomainError("Use Transfer to change the business unit.")
                if parsed["unit"]:
                    unit = await self.get(actor, parsed["unit"], "unit")
                    if unit["archived"] and (not before or parsed["unit"] != before.get("unit")):
                        raise DomainError("This business unit is archived.")
                if parsed["source"]:
                    source = await self.get(actor, parsed["source"], "source")
                    if source["archived"] and parsed["source"] != (before or {}).get("source"):
                        raise DomainError("This source is archived. Select an active source.")
                await self._owner(parsed["owner"], parsed["unit"])
            elif table in {"task", "note", "attachment"}:
                deal = await self.get(actor, parsed["deal"], "deal")
                if table == "task":
                    await self._owner(parsed["owner"], deal.get("unit"))
                if table == "note":
                    parsed["actor"] = (before or {}).get("actor", actor.id)
                if table == "attachment":
                    if before and before.get("storage_key"):
                        if parsed["url"]:
                            raise DomainError("A file cannot be converted into a link.")
                    elif not parsed["url"]:
                        raise DomainError("Enter a URL or upload a file.")
                    if not before:
                        parsed.update(created_by=actor.id, creator_name=user["name"])
            elif table in {"contact", "organization"} and not before and not user["admin"]:
                if not context_deal:
                    raise DomainError("Create this record from an accessible deal.")
            if parsed.get("organization"):
                org = await self.get(actor, parsed["organization"], "organization")
                if org["archived"] and (not before or parsed["organization"] != before.get("organization")):
                    raise DomainError("This organization is archived.")
            if table == "user":
                for unit_id in parsed["units"]:
                    await self.get(actor, unit_id, "unit")
                if before and before["admin"] and (not parsed["admin"] or not parsed["active"]):
                    admins = await self.db.rows(
                        "SELECT VALUE id FROM user WHERE active = true AND admin = true;"
                    )
                    if len(admins) == 1:
                        raise DomainError("Keep at least one active administrator.")
                if before and (
                    not parsed["active"]
                    or parsed["units"] != before["units"]
                    or parsed["admin"] != before["admin"]
                ):
                    # Reassignment is required before removing access; all owned records are checked below.
                    own_deals = await self.db.rows(
                        "SELECT * FROM deal WHERE owner = $user;", {"user": ensure_record_id(id)}
                    )
                    own_tasks = await self.db.rows(
                        "SELECT * FROM task WHERE owner = $user;", {"user": ensure_record_id(id)}
                    )
                    for row in own_deals + own_tasks:
                        d = row if row["id"].startswith("deal:") else await self.db.get(row["deal"])
                        if not parsed["active"] or not (parsed["admin"] or d.get("unit") in parsed["units"]):
                            raise DomainError("Reassign this owner’s deals and tasks before removing access.")
            record_id = id or new_id(table)
            after = (before or {}) | parsed
            changes = [(record_id, before, after)]
            if table == "task" and parsed["primary"] and not parsed["done"]:
                others = await self.db.rows(
                    "SELECT * FROM task WHERE deal = $deal AND primary = true AND done = false;",
                    {"deal": ensure_record_id(parsed["deal"])},
                )
                changes.extend((r["id"], r, r | {"primary": False}) for r in others if r["id"] != record_id)
            if context_deal and not before and table in {"contact", "organization"}:
                deal = await self.get(actor, context_deal, "deal")
                if table == "contact":
                    changes.append((new_id("deal_contact"), None, {"deal": deal["id"], "contact": record_id}))
                else:
                    changes.append((deal["id"], deal, deal | {"organization": record_id}))
            return await self._commit(
                actor,
                changes,
                action,
                key=key,
                payload=payload,
                audit_deal=deal["id"] if deal and table not in {"contact", "organization"} else None,
            )

    async def link(self, actor, deal_id, contact_id, *, remove=False, key=""):
        async with self.db.lock:
            deal = await self.get(actor, deal_id, "deal")
            contact = await self.get(actor, contact_id, "contact")
            if contact["archived"] and not remove:
                raise DomainError("Restore this contact before linking it.")
            links = await self.db.rows(
                "SELECT * FROM deal_contact WHERE deal = $deal AND contact = $contact;",
                {"deal": ensure_record_id(deal_id), "contact": ensure_record_id(contact_id)},
            )
            if remove:
                if links:
                    # Deletion of a relationship does not delete the shared contact.
                    event = self._db_value(
                        {
                            "version": 1,
                            "created_at": now(),
                            "updated_at": now(),
                            "deal": deal_id,
                            "target": contact_id,
                            "action": "contact.unlink",
                            "actor": actor.id,
                            "actor_name": (await self.user(actor))["name"],
                            "channel": actor.channel,
                            "client": actor.client,
                            "changes": {"contact": contact_id},
                        }
                    )
                    await self.db.query(
                        "BEGIN; DELETE $id; CREATE audit_event CONTENT $event; COMMIT;",
                        {"id": ensure_record_id(links[0]["id"]), "event": event},
                    )
                return deal
            if links:
                return deal
            await self._commit(
                actor,
                [(new_id("deal_contact"), None, {"deal": deal_id, "contact": contact_id})],
                "contact.link",
                audit_deal=deal_id,
            )
            return await self.get(actor, deal_id)

    async def transfer(self, actor, id, unit, owner, version, key=""):
        async with self.db.lock:
            user = await self.user(actor)
            if not user["admin"]:
                raise DomainError("Only administrators can transfer deals.", 403, "forbidden")
            payload = {"id": id, "unit": unit, "owner": owner, "version": version}
            replay = await self._replay(actor, "deal.transfer", key, payload)
            if replay:
                return replay
            d = await self.get(actor, id, "deal")
            if d["version"] != version:
                raise DomainError("This deal changed. Reload the page.", 409, "conflict")
            if unit:
                u = await self.get(actor, unit, "unit")
                if u["archived"]:
                    raise DomainError("This business unit is archived.")
            await self._owner(owner, unit)
            if not owner:
                raise DomainError("Select an owner for the transfer.")
            changes = [(id, d, d | {"unit": unit, "owner": owner})]
            tasks = await self.db.rows(
                "SELECT * FROM task WHERE deal = $deal;", {"deal": ensure_record_id(id)}
            )
            for t in tasks:
                try:
                    await self._owner(t["owner"], unit)
                except DomainError:
                    changes.append((t["id"], t, t | {"owner": owner}))
            return await self._commit(
                actor, changes, "deal.transfer", audit_deal=id, key=key, payload=payload
            )

    async def analytics(
        self, actor, *, unit="", source="", created_from="", created_to="", include_archived=True
    ):
        from datetime import date
        from zoneinfo import ZoneInfo

        from .analytics import calculate, instant

        start = date.fromisoformat(created_from) if created_from else None
        end = date.fromisoformat(created_to) if created_to else None
        if start and end and start > end:
            raise DomainError("The start date must be before the end date.")
        user = await self.user(actor)
        params = {
            "actor": ensure_record_id(actor.id),
            "units": [ensure_record_id(u, "unit") for u in user.get("units", [])],
        }
        clauses = [self._predicate("deal", user)]
        if unit:
            if unit == "none":
                clauses.append("unit = NONE" if user["admin"] else "false")
            else:
                await self.get(actor, unit, "unit")
                params["unit"] = ensure_record_id(unit, "unit")
                clauses.append("unit = $unit")
        if source:
            if source == "none":
                clauses.append("source = NONE")
            else:
                await self.get(actor, source, "source")
                params["source"] = ensure_record_id(source, "source")
                clauses.append("source = $source")
        if not include_archived:
            clauses.append("archived = false")
        predicate = " AND ".join(f"({c})" for c in clauses)
        # One snapshot; no UI pagination cap and no audit events of inaccessible deals.
        result = await self.db.query(
            "BEGIN; LET $scope = (SELECT * FROM deal WHERE " + predicate + "); "
            "RETURN { deals: $scope, events: (SELECT * FROM audit_event WHERE target IN "
            "(SELECT VALUE <string>id FROM deal WHERE " + predicate + ")) }; COMMIT;",
            params,
        )
        snapshot = next(r for r in result if isinstance(r, dict) and "deals" in r)
        cohort = []
        for deal in snapshot["deals"]:
            stamp = instant(deal.get("created_at"))
            day = stamp.astimezone(ZoneInfo("America/Sao_Paulo")).date() if stamp else None
            if (start or end) and not day:
                continue
            if start and day < start or end and day > end:
                continue
            cohort.append(deal)
        report = calculate(cohort, snapshot["events"], datetime.now(UTC))
        report["filters"] = {
            "unit": unit,
            "source": source,
            "created_from": created_from,
            "created_to": created_to,
            "include_archived": include_archived,
        }
        return report

    async def history(self, actor, id, offset=0, limit=100):
        record = await self.get(actor, id)
        table = record["id"].split(":")[0]
        if table == "deal":
            return await self.listing(actor, "audit_event", deal=id, offset=offset, limit=limit)
        if table not in {"contact", "organization"}:
            raise DomainError("Activity is not available for this record type.")
        rows = await self.db.rows(
            "SELECT * FROM audit_event WHERE target = $target ORDER BY created_at DESC LIMIT $limit START $offset;",
            {"target": id, "limit": min(200, max(1, limit)), "offset": max(0, offset)},
        )
        allowed = {"name", "email", "phone", "domain", "archived"}
        # Shared profile history must not reveal references to other units or deals.
        for row in rows:
            row.pop("deal", None)
            row["changes"] = {
                side: {k: v for k, v in row["changes"].get(side, {}).items() if k in allowed}
                for side in ["before", "after"]
            }
        return rows

    async def timeline(self, actor, deal, offset=0, limit=100):
        await self.get(actor, deal, "deal")
        rows = await self.db.rows(
            """SELECT * FROM array::concat(
            (SELECT *, actor.name AS actor_name, 'note' AS kind FROM note WHERE deal = $deal),
            (SELECT *, 'event' AS kind FROM audit_event WHERE deal = $deal AND action != 'note.create')
        ) ORDER BY created_at DESC, id DESC LIMIT $limit START $offset;""",
            {
                "deal": ensure_record_id(deal, "deal"),
                "limit": min(200, max(1, limit)),
                "offset": max(0, offset),
            },
        )
        ids = [r["id"] for r in rows if r["kind"] == "note"]
        if ids:
            events = await self.db.rows(
                "SELECT * FROM audit_event WHERE target IN $ids AND action = 'note.create';", {"ids": ids}
            )
            authors = {e["target"]: e for e in events}
            for row in rows:
                if row["kind"] == "note":
                    event = authors.get(row["id"], {})
                    row.update({k: event.get(k, row.get(k, "")) for k in ["actor_name", "client", "channel"]})
        return rows
