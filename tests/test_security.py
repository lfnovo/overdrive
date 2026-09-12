import io

import pytest
from starlette.datastructures import UploadFile
from test_journey import setup

from overdrive.service import DomainError
from overdrive.storage import LocalStorage


async def test_revocation_closes_every_surface(system):
    app, c = system
    crm = app.state.crm
    provider = app.state.provider
    admin, _a, _b, alice, _bob, _both, da, db, _inbox = await setup(crm)
    org = await crm.save(alice, "organization", {"name": "Shared company"}, context_deal=da["id"])
    visible = await crm.save(
        alice, "contact", {"name": "Visible", "organization": org["id"]}, context_deal=da["id"]
    )
    await crm.save(
        admin, "contact", {"name": "Hidden colleague", "organization": org["id"]}, context_deal=db["id"]
    )
    assert [r["id"] for r in await crm.listing(alice, "contact", q="colleague")] == []
    assert (await crm.get(alice, org["id"]))["name"] == "Shared company"
    task = await crm.save(admin, "task", {"deal": da["id"], "title": "Only mine", "owner": admin.id})
    assert await crm.listing(alice, "task", mine=True) == []
    file = await LocalStorage(app.state.settings.storage_path, 100).save(
        crm, alice, da["id"], UploadFile(filename="test.pdf", file=io.BytesIO(b"%PDF-test"))
    )
    token = await provider._tokens(alice.id, "security-client", ["crm"], app.state.settings.app_url + "/mcp")
    ticket = await provider.file_ticket(token.access_token, da["id"], file["id"])
    assert (await c.get(ticket["url"])).content == b"%PDF-test"
    before = await c.get("/api/deal", headers={"Authorization": "Bearer " + token.access_token})
    assert len(before.json()) == 1
    user = await crm.get(admin, alice.id)
    await crm.save(admin, "user", {"units": []}, id=alice.id, version=user["version"])
    after = await c.get("/api/deal", headers={"Authorization": "Bearer " + token.access_token})
    assert after.json() == []
    assert (await c.get(ticket["url"])).status_code == 404
    for table, id in [
        ("deal", da["id"]),
        ("contact", visible["id"]),
        ("organization", org["id"]),
        ("task", task["id"]),
        ("attachment", file["id"]),
    ]:
        r = await c.get("/api/" + table + "/" + id, headers={"Authorization": "Bearer " + token.access_token})
        assert r.status_code == 404, (table, r.text)
    user = await crm.get(admin, alice.id)
    await crm.save(admin, "user", {"active": False}, id=alice.id, version=user["version"])
    assert (
        await c.get("/api/deal", headers={"Authorization": "Bearer " + token.access_token})
    ).status_code == 401


async def test_upload_links_are_scoped_and_single_use(system):
    app, c = system
    crm = app.state.crm
    p = app.state.provider
    _admin, _a, _b, alice, _bob, _both, da, db, _inbox = await setup(crm)
    token = await p._tokens(alice.id, "files-client", ["crm"], app.state.settings.app_url + "/mcp")
    ticket = await p.file_ticket(token.access_token, da["id"])
    wrong = ticket["url"].replace(da["id"], db["id"])
    assert (await c.post(wrong, files={"file": ("x.pdf", b"bytes")})).status_code == 403
    upload = await c.post(ticket["url"], files={"file": ("x.pdf", b"bytes", "application/pdf")})
    assert upload.status_code == 200, upload.text
    assert upload.json()["size"] == 5
    assert (await c.post(ticket["url"], files={"file": ("x.pdf", b"bytes")})).status_code == 401
    download = await p.file_ticket(token.access_token, da["id"], upload.json()["id"])
    await p.revoke_token(await p.load_access_token(token.access_token))
    assert (await c.get(download["url"])).status_code == 401


async def test_fields_validation_and_atomic_failure(system):
    app, _ = system
    crm = app.state.crm
    admin, _a, b, alice, bob, _both, da, _db, _inbox = await setup(crm)
    before_events = await crm.listing(admin, "audit_event", deal=da["id"])
    for changes in [
        {"admin": True},
        {"unit": b["id"]},
        {"value_cents": -1},
        {"stage": "invalid"},
        {"owner": bob.id},
    ]:
        with pytest.raises(DomainError):
            await crm.save(alice, "deal", changes, id=da["id"], version=da["version"])
    assert len(await crm.listing(admin, "audit_event", deal=da["id"])) == len(before_events)
    assert (await crm.get(admin, da["id"]))["version"] == 1
    note = await crm.save(alice, "note", {"deal": da["id"], "content": "Original"})
    await crm.save(alice, "note", {"content": "Revised"}, id=note["id"], version=note["version"])
    events = await crm.listing(admin, "audit_event", deal=da["id"])
    revision = next(e for e in events if e["action"] == "note.update")
    assert revision["changes"]["before"]["content"] == "Original"
    assert revision["changes"]["after"]["content"] == "Revised"
    with pytest.raises(DomainError):
        await crm.save(alice, "audit_event", {"action": "forge"})
    with pytest.raises(DomainError):
        await crm.save(admin, "user", {"active": False}, id=admin.id, version=1)
