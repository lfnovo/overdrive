import asyncio
import re

import pytest

from overdrive.service import Actor, DomainError


async def setup(crm):
    admin = Actor("user:initial_admin")
    a = await crm.save(admin, "unit", {"name": "Consultoria"})
    b = await crm.save(admin, "unit", {"name": "Produtos"})
    alice = await crm.save(
        admin, "user", {"name": "Alice", "email": "alice@example.test", "units": [a["id"]]}
    )
    bob = await crm.save(admin, "user", {"name": "Bob", "email": "bob@example.test", "units": [b["id"]]})
    both = await crm.save(
        admin, "user", {"name": "Both", "email": "both@example.test", "units": [a["id"], b["id"]]}
    )
    da = await crm.save(
        admin, "deal", {"title": "Atlas", "unit": a["id"], "owner": admin.id, "value_cents": 4800000}
    )
    db = await crm.save(admin, "deal", {"title": "Hidden", "unit": b["id"], "owner": bob["id"]})
    inbox = await crm.save(admin, "deal", {"title": "Inbox"})
    return admin, a, b, Actor(alice["id"]), Actor(bob["id"]), Actor(both["id"]), da, db, inbox


async def test_access_and_shared_contacts(system):
    app, _ = system
    crm = app.state.crm
    admin, _a, _b, alice, bob, both, da, db, inbox = await setup(crm)
    shared = await crm.save(alice, "contact", {"name": "Marina"}, context_deal=da["id"])
    await crm.link(admin, db["id"], shared["id"])
    hidden = await crm.save(bob, "contact", {"name": "Private"}, context_deal=db["id"])
    assert [r["id"] for r in await crm.listing(alice, "deal")] == [da["id"]]
    assert {r["id"] for r in await crm.listing(both, "deal")} == {da["id"], db["id"]}
    assert [r["id"] for r in await crm.listing(alice, "contact")] == [shared["id"]]
    for id in [inbox["id"], db["id"], hidden["id"]]:
        with pytest.raises(DomainError) as exc:
            await crm.get(alice, id)
        assert exc.value.status == 404
    with pytest.raises(DomainError):
        await crm.save(alice, "deal", {"title": "Unassigned"})
    with pytest.raises(DomainError):
        await crm.save(alice, "user", {"admin": True}, id=alice.id, version=1)
    with pytest.raises(DomainError):
        await crm.save(alice, "contact", {"name": "Orphan"})
    shared = await crm.save(alice, "contact", {"phone": "123"}, id=shared["id"], version=shared["version"])
    assert (await crm.get(bob, shared["id"]))["phone"] == "123"
    # Archiving preserves graph access.
    da = await crm.save(alice, "deal", {"archived": True}, id=da["id"], version=da["version"])
    assert (await crm.get(alice, shared["id"]))["phone"] == "123"
    assert await crm.listing(alice, "deal") == []
    assert len(await crm.listing(alice, "deal", archived=True)) == 1


async def test_commercial_lifecycle_and_restore(system):
    app, _ = system
    crm = app.state.crm
    admin = Actor("user:initial_admin")
    deal = await crm.save(admin, "deal", {"title": "Lifecycle"})
    for patch in [
        {"stage": "Briefing Ready"},
        {"stage": "Proposal Ready"},
        {"stage": "Negotiation"},
        {"outcome": "lost", "loss_reason": "Prazo"},
        {"outcome": "open", "loss_reason": ""},
        {"outcome": "won"},
        {"archived": True},
        {"archived": False},
    ]:
        deal = await crm.save(admin, "deal", patch, id=deal["id"], version=deal["version"])
        assert all(deal[key] == value for key, value in patch.items())
    assert (await crm.listing(admin, "deal", outcome="won"))[0]["id"] == deal["id"]
    assert await crm.listing(admin, "deal", outcome="open") == []
    events = await crm.listing(admin, "audit_event", deal=deal["id"])
    assert len(events) == 9


async def test_versions_idempotency_tasks_materials_transfer(system):
    app, _ = system
    crm = app.state.crm
    admin, _a, b, alice, bob, _both, da, _db, _inbox = await setup(crm)
    task = await crm.save(
        alice,
        "task",
        {"deal": da["id"], "title": "Confirmar", "owner": alice.id, "primary": True},
        key="task1",
    )
    again = await crm.save(
        alice,
        "task",
        {"deal": da["id"], "title": "Confirmar", "owner": alice.id, "primary": True},
        key="task1",
    )
    assert task == again
    with pytest.raises(DomainError) as exc:
        await crm.save(alice, "task", {"deal": da["id"], "title": "Other", "owner": alice.id}, key="task1")
    assert exc.value.status == 409
    results = await asyncio.gather(
        crm.save(alice, "deal", {"title": "A"}, id=da["id"], version=1),
        crm.save(alice, "deal", {"title": "B"}, id=da["id"], version=1),
        return_exceptions=True,
    )
    assert sum(isinstance(r, DomainError) for r in results) == 1
    material = await crm.save(
        alice,
        "attachment",
        {"deal": da["id"], "title": "Escopo", "url": "https://example.test/scope"},
        key="link1",
    )
    deal = await crm.get(admin, da["id"])
    await crm.transfer(admin, da["id"], b["id"], bob.id, deal["version"])
    assert (await crm.get(bob, task["id"]))["owner"] == bob.id
    for table in ["task", "attachment", "audit_event"]:
        assert await crm.listing(alice, table) == []
    assert (await crm.get(bob, material["id"]))["title"] == "Escopo"


async def test_browser_api_upload_csrf(system):
    _app, c = system
    login = await c.get("/login")
    assert login.status_code == 200, login.text
    csrf = re.search(r'name="csrf" value="([^"]+)"', login.text)[1]
    response = await c.post("/auth/dev", data={"csrf": csrf})
    assert response.status_code == 303, response.text
    page = await c.get("/deals")
    assert page.status_code == 200, page.text
    csrf = re.search(r'name="csrf-token" content="([^"]+)"', page.text)[1]
    denied = await c.post("/api/deal", json={"data": {"title": "CSRF"}})
    assert denied.status_code == 403
    headers = {"X-CSRF-Token": csrf, "Idempotency-Key": "create"}
    r = await c.post(
        "/api/deal", json={"data": {"title": "Jornada local", "value_cents": 500000}}, headers=headers
    )
    assert r.status_code == 200, r.text
    d = r.json()
    note = await c.post(
        "/api/note",
        json={"data": {"deal": d["id"], "content": "<script>alert(1)</script> **seguro**"}},
        headers={"X-CSRF-Token": csrf},
    )
    assert note.status_code == 200, note.text
    content = b"%PDF-1.4\nlocal-test\n%%EOF"
    upload = await c.post(
        "/api/deals/" + d["id"] + "/files",
        files={"file": ("proposal.pdf", content, "application/pdf")},
        headers={"X-CSRF-Token": csrf, "Accept": "application/json", "Idempotency-Key": "file1"},
    )
    assert upload.status_code == 200, upload.text
    download = await c.get("/files/" + upload.json()["id"])
    assert download.content == content
    for path in [
        "/deals/" + d["id"],
        "/deals/" + d["id"] + "?tab=files",
        "/tasks",
        "/directory/contact",
        "/directory/organization",
        "/settings",
        "/new/deal",
        "/new/task?deal=" + d["id"],
        "/new/attachment?deal=" + d["id"],
    ]:
        result = await c.get(path)
        assert result.status_code == 200, (path, result.text)
    detail = await c.get("/deals/" + d["id"])
    assert "<script>alert(1)</script>" not in detail.text
    metadata = await c.get("/.well-known/oauth-authorization-server")
    assert metadata.status_code == 200, metadata.text
    protected = await c.post("/mcp", json={})
    assert protected.status_code == 401


async def test_timeline_pagination_and_authorship(system):
    app, _ = system
    crm = app.state.crm
    a = Actor("user:initial_admin")
    d = await crm.save(a, "deal", {"title": "Timeline"})
    for i in range(4):
        await crm.save(Actor(a.id, "mcp", "test-agent"), "note", {"deal": d["id"], "content": f"Note {i}"})
    first = await crm.timeline(a, d["id"], limit=2)
    second = await crm.timeline(a, d["id"], offset=2, limit=2)
    assert {r["id"] for r in first}.isdisjoint({r["id"] for r in second})
    assert all(r["kind"] == "note" and r["channel"] == "mcp" for r in first + second)
    assert first[0]["content"] == "Note 3" and second[1]["content"] == "Note 0"


async def test_replay_preserves_original_response_and_current_state(system):
    app, _ = system
    crm = app.state.crm
    a = Actor("user:initial_admin")
    original = await crm.save(a, "deal", {"title": "Original"}, key="stable-key")
    await crm.save(a, "deal", {"title": "Updated"}, id=original["id"], version=original["version"])
    replay = await crm.save(a, "deal", {"title": "Original"}, key="stable-key")
    assert replay == original
    assert (await crm.get(a, original["id"]))["title"] == "Updated"
