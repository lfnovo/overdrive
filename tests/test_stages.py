import re

import pytest

from overdrive.database import ensure_record_id
from overdrive.service import Actor, DomainError
from overdrive.stage_migration import migrate_stages
from overdrive.stages import LEGACY_STAGES, STAGES


async def test_stage_api_forms_and_migration(system):
    app, c = system
    crm = app.state.crm
    actor = Actor("user:initial_admin")
    d = await crm.save(actor, "deal", {"title": "Stage test"})
    assert d["stage"] == "New Lead"
    login = await c.get("/login")
    csrf = re.search(r'name="csrf" value="([^"]+)"', login.text)[1]
    await c.post("/auth/dev", data={"csrf": csrf})
    page = await c.get("/deals")
    csrf = re.search(r'name="csrf-token" content="([^"]+)"', page.text)[1]
    assert all(f'data-stage="{stage}"' in page.text for stage in STAGES)
    assert 'draggable="true"' in page.text
    path = "/api/deal/" + d["id"]
    headers = {"X-CSRF-Token": csrf}
    for stage in STAGES[1:]:
        response = await c.patch(
            path, json={"data": {"stage": stage}, "version": d["version"]}, headers=headers
        )
        assert response.status_code == 200, response.text
        d = response.json()
        assert d["stage"] == stage and d["outcome"] == "open"
    stale = await c.patch(path, json={"data": {"stage": "New Lead"}, "version": 1}, headers=headers)
    assert stale.status_code == 409
    forbidden = await c.patch(path, json={"data": {"stage": "New Lead"}, "version": d["version"]})
    assert forbidden.status_code == 403
    moved = await c.post(
        "/deals/" + d["id"] + "/stage",
        data={
            "csrf": csrf,
            "stage": "Briefing Ready",
            "version": d["version"],
            "key": "fallback",
            "return": "/deals?unit=none&view=board",
        },
    )
    assert moved.status_code == 303 and moved.headers["location"] == "/deals?unit=none&view=board"
    for old in LEGACY_STAGES:
        record = await crm.save(actor, "deal", {"title": old})
        await crm.db.query(
            "UPDATE $id SET stage = $stage;", {"id": ensure_record_id(record["id"]), "stage": old}
        )
    assert await migrate_stages(crm) == {"deals_updated": 4}
    assert await migrate_stages(crm) == {"deals_updated": 0}
    rows = await crm.listing(actor, "deal")
    assert all(r["stage"] == LEGACY_STAGES[r["title"]] for r in rows if r["title"] in LEGACY_STAGES)
    with pytest.raises(DomainError):
        await crm.save(actor, "deal", {"title": "Invalid", "stage": "Lead"})
    history = await crm.listing(actor, "audit_event", deal=d["id"])
    assert any(e["changes"]["after"].get("stage") == "Contract" for e in history)
