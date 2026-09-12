import re

from overdrive.service import Actor


def field(html, name):
    return re.search(rf'name="{name}" value="([^"]*)"', html)[1]


async def test_contextual_edit_partial_save_conflict_and_permissions(system):
    app, client = system
    crm = app.state.crm
    admin = Actor("user:initial_admin")
    deal = await crm.save(admin, "deal", {"title": "Before", "value_cents": 12345})
    login = await client.get("/login")
    await client.post("/auth/dev", data={"csrf": field(login.text, "csrf")})
    headers = {"X-Overdrive-Editor": "true"}
    path = f"/edit/{deal['id']}?field=title"
    response = await client.get(path, headers=headers)
    assert response.status_code == 200
    assert "<!doctype" not in response.text
    assert 'name="title"' in response.text
    assert 'name="value_cents"' not in response.text
    payload = {k: field(response.text, k) for k in ("csrf", "id", "key", "version", "_field")}
    payload["title"] = "After"
    result = await client.post("/save/deal", headers=headers, data=payload)
    assert result.status_code == 200, result.text
    assert result.json()["record"]["title"] == "After"
    current = await crm.get(admin, deal["id"])
    assert current["value_cents"] == 12345
    assert current.get("owner") is None
    stale = await client.post(
        "/save/deal", headers=headers, data={**payload, "title": "My draft", "key": "stale-edit"}
    )
    assert stale.status_code == 409
    assert "My draft" in stale.text
    assert "conflict-ack" in stale.text
    assert field(stale.text, "version") == str(current["version"])
    assert 'name="value_cents"' not in stale.text
    full = await client.get(f"/edit/{deal['id']}")
    assert "<!doctype" in full.text
    assert f'<option value="{admin.id}" selected' not in full.text
    forbidden = await client.get(f"/edit/{deal['id']}?field=unit", headers=headers)
    assert forbidden.status_code == 400
    outcome = await client.get(f"/edit/{deal['id']}?field=outcome", headers=headers)
    assert 'name="outcome"' in outcome.text and 'name="loss_reason"' in outcome.text
    no_csrf = await client.post("/save/deal", headers=headers, data={**payload, "csrf": ""})
    assert no_csrf.status_code == 403


async def test_relationship_pages_and_editable_directory(system):
    app, client = system
    crm = app.state.crm
    admin = Actor("user:initial_admin")
    org = await crm.save(admin, "organization", {"name": "Example studio", "domain": "example.test"})
    contact = await crm.save(admin, "contact", {"name": "Example person", "organization": org["id"]})
    login = await client.get("/login")
    await client.post("/auth/dev", data={"csrf": field(login.text, "csrf")})
    for row in (org, contact):
        profile = await client.get("/records/" + row["id"])
        assert profile.status_code == 200, profile.text
        assert "In good company" in profile.text
        assert "data-inline" in profile.text
        assert "Work in motion" in profile.text
    directory = await client.get("/directory/contact")
    assert "data-inline" in directory.text
    page = await client.get("/new/contact", headers={"X-Overdrive-Editor": "true"})
    assert page.status_code == 200
    assert "<!doctype" not in page.text
