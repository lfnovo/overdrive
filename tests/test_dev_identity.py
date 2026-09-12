import re

from overdrive.service import Actor


async def test_development_login_uses_member_permissions(system):
    app, client = system
    crm = app.state.crm
    admin = Actor("user:initial_admin")
    unit = await crm.save(admin, "unit", {"name": "Member unit"})
    member = await crm.save(
        admin, "user", {"name": "Test member", "email": "member@example.test", "units": [unit["id"]]}
    )
    visible = await crm.save(admin, "deal", {"title": "Visible", "unit": unit["id"]})
    hidden = await crm.save(admin, "deal", {"title": "Admin only"})
    app.state.settings.dev_login_email = member["email"]
    page = await client.get("/login")
    assert "Continue as Test member" in page.text
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text)[1]
    assert (await client.post("/auth/dev", data={"csrf": csrf})).status_code == 303
    assert (await client.get(f"/deals/{visible['id']}")).status_code == 200
    assert (await client.get(f"/deals/{hidden['id']}")).status_code == 404
    assert (await client.get(f"/deals/{visible['id']}/transfer")).status_code == 403
    assert not (await crm.get(admin, member["id"]))["admin"]
