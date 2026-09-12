import os
import re

from surrealdb import AsyncSurreal

from overdrive.database import ensure_record_id
from overdrive.service import Actor


def field(html, name):
    return re.search(rf'name="{name}" value="([^"]*)"', html)[1]


async def test_transfer_legacy_deal_preserves_owner_and_handles_conflict(system):
    app, client = system
    crm = app.state.crm
    admin = Actor("user:initial_admin")
    source = await crm.save(admin, "unit", {"name": "Source"})
    destination = await crm.save(admin, "unit", {"name": "Destination"})
    other = await crm.save(
        admin,
        "user",
        {"name": "A first alphabetical owner", "email": "first@example.test", "units": [source["id"]]},
    )
    deal = await crm.save(
        admin, "deal", {"title": "Legacy transfer", "unit": source["id"], "owner": admin.id}
    )
    task = await crm.save(admin, "task", {"deal": deal["id"], "title": "Keep moving", "owner": other["id"]})
    settings = app.state.settings
    async with AsyncSurreal(settings.surreal_url) as root:
        await root.signin(
            {"username": os.environ["BOOTSTRAP_USER"], "password": os.environ["BOOTSTRAP_PASS"]}
        )
        await root.use(settings.surreal_namespace, settings.surreal_database)
        await root.query("DEFINE FIELD OVERWRITE deal_so_far ON deal TYPE option<string>;")
        await root.query("UPDATE $id UNSET deal_so_far;", {"id": ensure_record_id(deal["id"])})
        await root.query('DEFINE FIELD OVERWRITE deal_so_far ON deal TYPE string DEFAULT "";')
    assert "deal_so_far" not in await crm.get(admin, deal["id"])
    login = await client.get("/login")
    await client.post("/auth/dev", data={"csrf": field(login.text, "csrf")})
    path = f"/deals/{deal['id']}/transfer"
    form = await client.get(path)
    assert f'<option value="{admin.id}" selected>' in form.text
    assert f'<option value="{other["id"]}" selected>' not in form.text
    response = await client.post(
        path,
        data={
            "csrf": field(form.text, "csrf"),
            "key": field(form.text, "key"),
            "version": field(form.text, "version"),
            "unit": destination["id"],
            "owner": admin.id,
        },
    )
    assert response.status_code == 303, response.text
    updated = await crm.get(admin, deal["id"])
    assert updated["owner"] == admin.id and updated["unit"] == destination["id"]
    assert updated["deal_so_far"] == ""
    assert (await crm.get(admin, task["id"]))["owner"] == admin.id
    stale = await client.post(
        path,
        data={
            "csrf": field(form.text, "csrf"),
            "key": "stale-transfer",
            "version": field(form.text, "version"),
            "unit": source["id"],
            "owner": admin.id,
        },
    )
    assert stale.status_code == 409
    assert f'<option value="{source["id"]}" selected>' in stale.text
    assert f'<option value="{admin.id}" selected>' in stale.text
    assert field(stale.text, "version") == str(updated["version"])
    invalid = await client.post(
        path,
        data={
            "csrf": field(form.text, "csrf"),
            "key": "invalid-owner",
            "version": str(updated["version"]),
            "unit": destination["id"],
            "owner": other["id"],
        },
    )
    assert invalid.status_code == 400
    assert "The owner must have access" in invalid.text
    assert f'<option value="{other["id"]}" selected>' in invalid.text
    assert (await crm.get(admin, deal["id"]))["owner"] == admin.id
