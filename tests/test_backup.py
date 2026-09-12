import io

import pytest
from starlette.datastructures import UploadFile

from overdrive.backup import TABLES, export_backup, restore_backup
from overdrive.service import Actor
from overdrive.storage import LocalStorage


async def test_complete_backup_restore(system, tmp_path):
    app, _ = system
    crm = app.state.crm
    db = app.state.db
    s = app.state.settings
    a = Actor("user:initial_admin")
    source = await crm.save(a, "source", {"name": "Event"})
    deal = await crm.save(a, "deal", {"title": "Backup", "source": source["id"]})
    note = await crm.save(a, "note", {"deal": deal["id"], "content": "Preserve history"})
    storage = LocalStorage(s.storage_path, s.max_upload_bytes)
    attachment = await storage.save(
        crm, a, deal["id"], UploadFile(filename="x.pdf", file=io.BytesIO(b"PDF roundtrip"))
    )
    link = await crm.save(
        a,
        "attachment",
        {"deal": deal["id"], "title": "Site", "url": "https://example.test", "deprecated": True},
    )
    auth = app.state.local_auth
    from urllib.parse import parse_qs, urlsplit

    secret = parse_qs(urlsplit(await auth.issue(a.id)).query)["token"][0]
    password = "a backup test passphrase"
    await auth.activate(secret, password, "backup-test")
    before_epoch = await auth.epoch(a.id)
    target = tmp_path / "backup.zip"
    counts = await export_backup(db, s, target)
    assert counts["deal"] == 1 and counts["attachment"] == 2
    with pytest.raises(ValueError):
        await restore_backup(db, s, target)
    # The fixture is an isolated disposable database. Emulate restoring into a fresh installation.
    await db.query("BEGIN; " + " ".join(f"DELETE {t};" for t in TABLES + ["auth_record"]) + " COMMIT;")
    storage.path(attachment["storage_key"]).unlink()
    restored = await restore_backup(db, s, target)
    assert restored == counts
    assert (await crm.get(a, deal["id"]))["source"] == source["id"]
    assert (await crm.get(a, source["id"]))["name"] == "Event"
    assert (await crm.get(a, link["id"]))["deprecated"] is True
    assert (await crm.get(a, note["id"]))["content"] == "Preserve history"
    assert storage.path(attachment["storage_key"]).read_bytes() == b"PDF roundtrip"
    assert await db.rows("SELECT * FROM auth_record;") == []

    restored_user, restored_epoch = await auth.authenticate("admin@example.test", password, "restore-test")
    assert restored_user["id"] == a.id
    assert restored_epoch != before_epoch
    assert (await auth.credential(a.id))["activation_hash"] == ""
