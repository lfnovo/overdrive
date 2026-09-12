import re

import pytest

from overdrive.service import Actor, DomainError


async def test_sources_admin_catalog_and_member_assignment(system):
    app, client = system
    crm = app.state.crm
    admin = Actor("user:initial_admin")
    source = await crm.save(admin, "source", {"name": "Referral"})
    unit = await crm.save(admin, "unit", {"name": "Team"})
    member = await crm.save(
        admin, "user", {"name": "Member", "email": "member@example.test", "units": [unit["id"]]}
    )
    actor = Actor(member["id"])
    assert (await crm.listing(actor, "source"))[0]["name"] == "Referral"
    with pytest.raises(DomainError) as error:
        await crm.save(actor, "source", {"name": "Unauthorized"})
    assert error.value.status == 403
    deal = await crm.save(
        actor, "deal", {"title": "Referral deal", "unit": unit["id"], "source": source["id"]}
    )
    assert deal["source"] == source["id"]
    with pytest.raises(DomainError):
        await crm.save(actor, "deal", {"source": "source:missing"}, id=deal["id"], version=deal["version"])
    source = await crm.save(admin, "source", {"archived": True}, id=source["id"], version=source["version"])
    assert await crm.listing(actor, "source") == []
    deal = await crm.save(actor, "deal", {"title": "Still editable"}, id=deal["id"], version=deal["version"])
    assert deal["source"] == source["id"]
    with pytest.raises(DomainError):
        await crm.save(actor, "deal", {"title": "New", "unit": unit["id"], "source": source["id"]})
    login = await client.get("/login")
    csrf = re.search(r'name="csrf" value="([^"]+)"', login.text)[1]
    await client.post("/auth/dev", data={"csrf": csrf})
    settings = await client.get("/settings")
    assert "Deal sources" in settings.text and "Referral" in settings.text
    form = await client.get(f"/edit/{deal['id']}")
    assert f'value="{source["id"]}" selected' in form.text
    assert "Referral (archived)" in form.text
    cleared = await crm.save(actor, "deal", {"source": None}, id=deal["id"], version=deal["version"])
    assert not cleared.get("source")
    assert source["id"] not in (await client.get("/new/deal")).text

    app.state.settings.dev_login_email = member["email"]
    login = await client.get("/login")
    csrf = re.search(r'name="csrf" value="([^"]+)"', login.text)[1]
    await client.post("/auth/dev", data={"csrf": csrf})
    assert "Deal sources" not in (await client.get("/settings")).text
    assert (await client.get("/new/source")).status_code == 403
