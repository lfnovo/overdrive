import json

import pytest
from test_journey import setup
from test_oauth_mcp import authorize, login

from overdrive.service import Actor, DomainError


async def call(client, headers, name, arguments):
    response = await client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    text = result["content"][0]["text"] if result.get("content") else "{}"
    return result, {} if result.get("isError") else json.loads(text)


async def test_mcp_deletes_task_without_completing_it(system):
    app, client = system
    crm = app.state.crm
    admin = Actor("user:initial_admin")
    csrf = await login(client)
    _, tokens = await authorize(client, csrf)
    headers = {
        "Authorization": "Bearer " + tokens["access_token"],
        "Accept": "application/json, text/event-stream",
    }
    deal = await crm.save(admin, "deal", {"title": "Closed deal"})
    obsolete = await crm.save(
        admin,
        "task",
        {"deal": deal["id"], "title": "Obsolete follow-up", "owner": admin.id, "due": "2020-01-01"},
    )
    arguments = {
        "task_id": obsolete["id"],
        "expected_version": obsolete["version"],
        "idempotency_key": "del-1",
    }
    result, deleted = await call(client, headers, "delete_task", arguments)
    assert not result.get("isError"), result
    assert deleted["deleted"] is True and deleted["done"] is False
    # Retrying the same request is idempotent and does not create another audit event.
    result, again = await call(client, headers, "delete_task", arguments)
    assert not result.get("isError") and again["version"] == deleted["version"]
    # A stale version with a new key is a conflict, not a silent overwrite.
    result, _ = await call(client, headers, "delete_task", arguments | {"idempotency_key": "del-2"})
    assert result.get("isError") and "changed" in json.dumps(result)
    # Pending, overdue, next-step and completed views exclude the task.
    for done in (False, True):
        assert await crm.listing(admin, "task", mine=True, done=done) == []
        page = await client.get("/tasks", params={"completed": str(done).lower()})
        assert "Obsolete follow-up" not in page.text
    assert await crm.listing(admin, "task", deal=deal["id"]) == []
    board = await client.get("/deals", params={"view": "board"})
    assert "Obsolete follow-up" not in board.text
    result, _ = await call(client, headers, "my_tasks", {})
    assert obsolete["id"] not in json.dumps(result)
    # Deleted tasks remain discoverable for restoration and the audit trail records the action.
    result, _ = await call(
        client, headers, "list_records", {"entity": "task", "deal_id": deal["id"], "deleted": True}
    )
    assert obsolete["id"] in json.dumps(result)
    events = await crm.listing(admin, "audit_event", deal=deal["id"])
    assert [e["action"] for e in events].count("task.delete") == 1
    with pytest.raises(DomainError, match="Restore this task"):
        await crm.save(admin, "task", {"title": "Edit"}, id=obsolete["id"], version=deleted["version"])
    result, restored = await call(
        client,
        headers,
        "restore_task",
        {"task_id": obsolete["id"], "expected_version": deleted["version"], "idempotency_key": "restore-1"},
    )
    assert not result.get("isError"), result
    assert restored["deleted"] is False and restored["done"] is False
    assert [t["id"] for t in await crm.listing(admin, "task", mine=True, done=False)] == [obsolete["id"]]
    assert "task.restore" in [e["action"] for e in await crm.listing(admin, "audit_event", deal=deal["id"])]


async def test_deleting_primary_task_can_promote_next_step(system):
    app, _client = system
    crm = app.state.crm
    admin = Actor("user:initial_admin")
    deal = await crm.save(admin, "deal", {"title": "Open deal"})
    primary = await crm.save(
        admin, "task", {"deal": deal["id"], "title": "Old step", "owner": admin.id, "primary": True}
    )
    other = await crm.save(admin, "task", {"deal": deal["id"], "title": "New step", "owner": admin.id})
    done = await crm.save(
        admin, "task", {"deal": deal["id"], "title": "Done step", "owner": admin.id, "done": True}
    )
    for invalid in (primary["id"], done["id"]):
        with pytest.raises(DomainError, match="another pending task"):
            await crm.delete_task(
                admin, primary["id"], primary["version"], key="x-" + invalid, next_primary=invalid
            )
    deleted = await crm.delete_task(
        admin, primary["id"], primary["version"], key="promote", next_primary=other["id"]
    )
    assert deleted["deleted"] is True and deleted["primary"] is False
    assert (await crm.get(admin, other["id"]))["primary"] is True
    events = await crm.listing(admin, "audit_event", deal=deal["id"])
    assert {(e["target"], e["action"]) for e in events} >= {
        (primary["id"], "task.delete"),
        (other["id"], "task.update"),
    }
    # Restoring does not displace the current next step.
    restored = await crm.delete_task(admin, primary["id"], deleted["version"], key="back", restore=True)
    assert restored["primary"] is False
    assert (await crm.get(admin, other["id"]))["primary"] is True
    with pytest.raises(DomainError, match="already active"):
        await crm.delete_task(admin, primary["id"], restored["version"], key="again", restore=True)
    # Without a replacement, the deal simply has no primary task; the board falls back to the earliest pending task.
    current = await crm.get(admin, other["id"])
    await crm.delete_task(admin, other["id"], current["version"], key="no-next")
    assert [t["id"] for t in await crm.listing(admin, "task", deal=deal["id"], done=False)] == [primary["id"]]
    with pytest.raises(DomainError, match="idempotency key"):
        await crm.delete_task(admin, primary["id"], restored["version"])


async def test_task_deletion_respects_deal_access(system):
    app, client = system
    crm = app.state.crm
    admin, _a, _b, alice, bob, _both, da, db, _inbox = await setup(crm)
    hidden = await crm.save(admin, "task", {"deal": db["id"], "title": "Bob only", "owner": bob.id})
    with pytest.raises(DomainError) as exc:
        await crm.delete_task(alice, hidden["id"], hidden["version"], key="steal")
    assert exc.value.status == 404
    mine = await crm.save(alice, "task", {"deal": da["id"], "title": "Alice task", "owner": alice.id})
    deleted = await crm.delete_task(alice, mine["id"], mine["version"], key="mine")
    assert deleted["deleted"] is True
    # A deleted task does not block removing its owner's access; restoring then requires a valid owner.
    user = await crm.get(admin, alice.id)
    await crm.save(admin, "user", {"units": []}, id=alice.id, version=user["version"])
    with pytest.raises(DomainError, match="owner must have access"):
        await crm.delete_task(admin, mine["id"], deleted["version"], key="restore", restore=True)
    # The HTTP API shares the same behavior.
    csrf = await login(client)
    other = await crm.save(admin, "task", {"deal": da["id"], "title": "API task", "owner": admin.id})
    response = await client.post(
        "/api/tasks/" + other["id"] + "/delete",
        json={"version": other["version"]},
        headers={"Idempotency-Key": "api-delete", "X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    assert response.json()["deleted"] is True
    listed = await client.get("/api/task", params={"deal": da["id"], "deleted": "true"})
    assert {t["id"] for t in listed.json()} == {mine["id"], other["id"]}
