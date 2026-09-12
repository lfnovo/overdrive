import io
import re

import pytest
from starlette.datastructures import UploadFile

from overdrive.database import ensure_record_id
from overdrive.material_migration import migrate_materials
from overdrive.service import Actor, DomainError
from overdrive.storage import LocalStorage


async def test_materials_status_security_and_concurrency(system):
    app, c = system
    crm, s = app.state.crm, app.state.settings
    a = Actor("user:initial_admin", "mcp", "test-agent")
    deal = await crm.save(a, "deal", {"title": "Materials"})
    payload = {"deal": deal["id"], "title": "Planilha", "url": "https://example.test/sheet"}
    first = await crm.save(a, "attachment", payload, key="link1")
    assert first == await crm.save(a, "attachment", payload, key="link1")
    assert first["creator_name"] and first["created_by"] == a.id
    storage = LocalStorage(s.storage_path, s.max_upload_bytes)
    content = b"a,b\n1,2"
    file = await storage.save(
        crm,
        a,
        deal["id"],
        UploadFile(filename="data.csv", file=io.BytesIO(content)),
        title="Dados",
        description="Levantamento",
    )
    assert not (await crm.get(a, first["id"]))["deprecated"]
    stale = await crm.save(a, "attachment", {"deprecated": True}, id=first["id"], version=1)
    assert [r["id"] for r in await crm.listing(a, "attachment", deal=deal["id"], deprecated=False)] == [
        file["id"]
    ]
    assert len(await crm.listing(a, "attachment", deal=deal["id"])) == 2
    with pytest.raises(DomainError) as exc:
        await crm.save(a, "attachment", {"deprecated": False}, id=first["id"], version=1)
    assert exc.value.status == 409
    valid = await crm.save(a, "attachment", {"deprecated": False}, id=first["id"], version=stale["version"])
    assert not valid["deprecated"]
    for changes in [{"storage_key": "a" * 32}, {"url": "https://example.test/replacement"}]:
        with pytest.raises(DomainError):
            await crm.save(a, "attachment", changes, id=file["id"], version=file["version"])
    for url in ["", "javascript:alert(1)", "data:text/html,test", "https://user:pass@example.test"]:
        with pytest.raises(DomainError):
            await crm.save(a, "attachment", payload | {"url": url})
    other = await crm.save(a, "deal", {"title": "Other"})
    with pytest.raises(DomainError):
        await crm.save(a, "attachment", {"deal": other["id"]}, id=first["id"], version=valid["version"])
    events = await crm.listing(a, "audit_event", deal=deal["id"])
    assert any(e["action"] == "attachment.update" and e["changes"]["after"]["deprecated"] for e in events)
    assert all(e["channel"] == "mcp" and e["client"] == "test-agent" for e in events)
    for table in ["proposal_version"]:
        with pytest.raises(DomainError):
            await crm.listing(a, table)
    # Exercise the actual forms, visibility filter, metadata editing and download.
    login = await c.get("/login")
    csrf = re.search(r'name="csrf" value="([^"]+)"', login.text)[1]
    await c.post("/auth/dev", data={"csrf": csrf})
    page = await c.get("/deals/" + deal["id"] + "?tab=files")
    csrf = re.search(r'name="csrf-token" content="([^"]+)"', page.text)[1]
    assert "Files & links" in page.text and "Current proposal" not in page.text
    response = await c.post(
        "/materials/" + file["id"] + "/status",
        data={"csrf": csrf, "version": file["version"], "deprecated": "true", "key": "status-ui"},
    )
    assert response.status_code == 303
    filtered = await c.get("/deals/" + deal["id"] + "?tab=files&hide_deprecated=true")
    materials = filtered.text.split('<section class="materials-pane">')[1].split("</section>")[0]
    assert "Levantamento" not in materials and "Planilha" in materials
    download = await c.get("/files/" + file["id"])
    assert download.content == content
    assert (await c.get("/files/" + first["id"])).status_code == 404
    edit = await c.get("/edit/" + file["id"])
    assert edit.status_code == 200 and 'name="url"' not in edit.text
    response = await c.post(
        "/save/attachment",
        data={
            "csrf": csrf,
            "id": file["id"],
            "version": 2,
            "title": "Dados revisados",
            "description": "Novo título",
            "deprecated": "false",
        },
    )
    assert response.status_code == 303, response.text
    assert (await crm.get(a, file["id"]))["title"] == "Dados revisados"


async def test_legacy_material_migration_is_lossless_and_repeatable(system):
    app, _ = system
    crm, s = app.state.crm, app.state.settings
    a = Actor("user:initial_admin")
    d = await crm.save(a, "deal", {"title": "Legacy"})
    storage = LocalStorage(s.storage_path, s.max_upload_bytes)
    f = await storage.save(crm, a, d["id"], UploadFile(filename="old.pdf", file=io.BytesIO(b"original")))
    # Reproduce the original schema's records, including an old attachment without metadata.
    await crm.db.query(
        "UPDATE $id SET title = '', description = '', url = '', deprecated = false, created_by = NONE, creator_name = '';",
        {"id": ensure_record_id(f["id"])},
    )
    for n in [1, 2]:
        await crm.db.query(
            "CREATE $id CONTENT $data;",
            {
                "id": ensure_record_id(f"proposal_version:old{n}"),
                "data": crm._db_value(
                    {
                        "version": 1,
                        "number": n,
                        "deal": d["id"],
                        "markdown": f"# conteúdo {n} — íntegro",
                        "gamma_url": "https://example.test/deck" if n == 2 else "",
                        "attachment": f["id"],
                        "created_at": "2026-01-01T10:00:00+00:00",
                        "updated_at": "2026-01-01T10:00:00+00:00",
                        "sent_at": "2026-01-02T10:00:00+00:00",
                    }
                ),
            },
        )
    await crm.db.query(
        "UPDATE $id SET current_proposal = $p;",
        {"id": ensure_record_id(d["id"]), "p": ensure_record_id("proposal_version:old2")},
    )
    report = await migrate_materials(crm, s)
    assert report == {"files_upgraded": 1, "proposals_preserved": 2, "items_created": 3}
    items = await crm.listing(a, "attachment", deal=d["id"])
    assert len(items) == 4
    old = next(r for r in items if r["title"] == "Document v1.md")
    assert old["deprecated"] and old["created_at"] == "2026-01-01T10:00:00+00:00"
    assert storage.path(old["storage_key"]).read_text() == "# conteúdo 1 — íntegro"
    assert not (await crm.get(a, f["id"]))["deprecated"]
    assert storage.path(f["storage_key"]).read_bytes() == b"original"
    assert "current_proposal" not in await crm.get(a, d["id"])
    legacy = await crm.db.get("proposal_version:old2")
    assert legacy["sent_at"] == "2026-01-02T10:00:00+00:00" and legacy["materials_migrated"]
    assert await migrate_materials(crm, s) == {
        "files_upgraded": 0,
        "proposals_preserved": 0,
        "items_created": 0,
    }
    assert await crm.listing(a, "attachment", deal=d["id"]) == items


async def test_delete_materials_permissions_retry_and_download(system, tmp_path):
    from overdrive.backup import export_backup

    app, c = system
    crm, s = app.state.crm, app.state.settings
    a = Actor("user:initial_admin")
    unit = await crm.save(a, "unit", {"name": "Visible"})
    member = await crm.save(
        a, "user", {"name": "Member", "email": "member@example.test", "units": [unit["id"]]}
    )
    outsider = await crm.save(a, "user", {"name": "Outsider", "email": "out@example.test"})
    deal = await crm.save(a, "deal", {"title": "Delete", "unit": unit["id"]})
    storage = LocalStorage(s.storage_path, s.max_upload_bytes)
    f = await storage.save(
        crm, a, deal["id"], UploadFile(filename="delete.txt", file=io.BytesIO(b"delete me"))
    )
    with pytest.raises(DomainError) as exc:
        await storage.delete(crm, Actor(outsider["id"]), f["id"], f["version"], "outside")
    assert exc.value.status == 404 and storage.path(f["storage_key"]).exists()
    with pytest.raises(DomainError) as exc:
        await storage.delete(crm, Actor(member["id"]), f["id"], 99, "stale")
    assert exc.value.status == 409
    agent = Actor(member["id"], "mcp", "delete-test")
    receipt = await storage.delete(crm, agent, f["id"], f["version"], "delete1")
    assert receipt["deleted"] and not storage.path(f["storage_key"]).exists()
    assert await storage.delete(crm, agent, f["id"], f["version"], "delete1") == receipt
    with pytest.raises(DomainError):
        await crm.get(a, f["id"])
    assert await crm.listing(a, "attachment", deal=deal["id"]) == []
    events = await crm.listing(a, "audit_event", deal=deal["id"])
    event = next(e for e in events if e["action"] == "attachment.delete")
    assert event["actor"] == member["id"] and event["client"] == "delete-test"
    assert event["changes"]["before"]["title"] == "delete.txt" and event["changes"]["after"] == {}
    counts = await export_backup(crm.db, s, tmp_path / "deleted-backup.zip")
    assert counts["attachment"] == 0
    await crm.save(a, "user", {"units": []}, id=member["id"], version=member["version"])
    with pytest.raises(DomainError):
        await storage.delete(crm, agent, f["id"], f["version"], "delete1")
    link = await crm.save(
        a, "attachment", {"deal": deal["id"], "title": "Link to delete", "url": "https://example.test"}
    )
    login = await c.get("/login")
    csrf = re.search(r'name="csrf" value="([^"]+)"', login.text)[1]
    await c.post("/auth/dev", data={"csrf": csrf})
    confirmation = await c.get("/materials/" + link["id"] + "/delete")
    assert confirmation.status_code == 200 and "Confirm deletion" in confirmation.text
    assert await crm.get(a, link["id"])  # GET never deletes.
    denied = await c.post("/materials/" + link["id"] + "/delete", data={"version": 1, "key": "ui-delete"})
    assert denied.status_code == 403
    csrf = re.search(r'name="csrf-token" content="([^"]+)"', confirmation.text)[1]
    deleted = await c.post(
        "/materials/" + link["id"] + "/delete", data={"csrf": csrf, "version": 1, "key": "ui-delete"}
    )
    assert deleted.status_code == 303
    assert (await c.get("/files/" + f["id"])).status_code == 404
    assert (await c.get("/api/attachment/" + link["id"])).status_code == 404


async def test_delete_cleanup_can_be_retried(system, monkeypatch):
    from pathlib import Path

    app, _ = system
    crm, s = app.state.crm, app.state.settings
    actor = Actor("user:initial_admin")
    d = await crm.save(actor, "deal", {"title": "Cleanup retry"})
    storage = LocalStorage(s.storage_path, s.max_upload_bytes)
    f = await storage.save(crm, actor, d["id"], UploadFile(filename="x.txt", file=io.BytesIO(b"x")))
    original = Path.unlink

    def fail(*args, **kwargs):
        raise OSError("Simulated disk error")

    monkeypatch.setattr(Path, "unlink", fail)
    with pytest.raises(DomainError) as exc:
        await storage.delete(crm, actor, f["id"], 1, "cleanup")
    assert exc.value.code == "file_cleanup_failed"
    assert await crm.listing(actor, "attachment", deal=d["id"]) == []
    monkeypatch.setattr(Path, "unlink", original)
    assert (await storage.delete(crm, actor, f["id"], 1, "cleanup"))["deleted"]
    assert not storage.path(f["storage_key"]).exists()
