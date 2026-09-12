from test_oauth_mcp import authorize, login

from overdrive.service import Actor


async def test_my_tasks_only_open_deals_in_ui_api_and_mcp(system):
    app, client = system
    crm = app.state.crm
    admin = Actor("user:initial_admin")
    csrf = await login(client)
    _, tokens = await authorize(client, csrf)
    headers = {
        "Authorization": "Bearer " + tokens["access_token"],
        "Accept": "application/json, text/event-stream",
    }
    deals, tasks = {}, {}
    # Closed-deal tasks are newer, so filtering after LIMIT would lose the open task.
    for name, fields in [
        ("Open", {}),
        ("Won", {"outcome": "won"}),
        ("Lost", {"outcome": "lost"}),
        ("Archived", {"archived": True}),
    ]:
        deals[name] = await crm.save(admin, "deal", {"title": name, **fields})
        for done in (False, True):
            tasks[name, done] = await crm.save(
                admin,
                "task",
                {"title": f"{name} work {done}", "deal": deals[name]["id"], "owner": admin.id, "done": done},
            )
    for done in (False, True):
        page = await client.get("/tasks", params={"completed": str(done).lower()})
        assert page.status_code == 200
        assert tasks["Open", done]["title"] in page.text
        for name in ("Won", "Lost", "Archived"):
            assert tasks[name, done]["title"] not in page.text
        api = await client.get("/api/task", params={"mine": "true", "done": str(done).lower(), "limit": 1})
        assert [t["id"] for t in api.json()] == [tasks["Open", done]["id"]]
        response = await client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "my_tasks", "arguments": {"completed": done, "limit": 1}},
            },
        )
        assert response.status_code == 200
        assert not response.json()["result"].get("isError")
        assert tasks["Open", done]["id"] in response.text
        for name in ("Won", "Lost", "Archived"):
            assert tasks[name, done]["id"] not in response.text
    assert await crm.listing(admin, "task", mine=True, done=False, offset=1, limit=1) == []
    for name in ("Won", "Lost", "Archived"):
        # Historical tasks remain available on the deal without being completed or removed.
        historical = await crm.listing(admin, "task", deal=deals[name]["id"])
        assert {t["id"] for t in historical} == {tasks[name, done]["id"] for done in (False, True)}
        page = await client.get("/deals/" + deals[name]["id"])
        assert page.status_code == 200 and tasks[name, False]["title"] in page.text
        await crm.save(
            admin,
            "deal",
            {"outcome": "open", "archived": False},
            id=deals[name]["id"],
            version=deals[name]["version"],
        )
    assert {t["id"] for t in await crm.listing(admin, "task", mine=True, done=False)} == {
        tasks[name, False]["id"] for name in deals
    }
