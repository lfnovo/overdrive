import re

import pytest

from overdrive.service import Actor, DomainError


async def test_living_summary_version_and_attribution(system):
    app, client = system
    crm = app.state.crm
    human = Actor("user:initial_admin")
    agent = Actor("user:initial_admin", channel="mcp", client="test-agent")
    deal = await crm.save(human, "deal", {"title": "Living context"})
    saved = await crm.save(
        agent,
        "deal",
        {"deal_so_far": "## Status\nWaiting for scope approval."},
        id=deal["id"],
        version=deal["version"],
        key="summary-1",
    )
    assert saved["deal_so_far_author"]
    assert saved["deal_so_far_updated_at"]
    with pytest.raises(DomainError) as error:
        await crm.save(human, "deal", {"deal_so_far": "stale draft"}, id=deal["id"], version=deal["version"])
    assert error.value.status == 409
    edited = await crm.save(human, "deal", {"title": "New title"}, id=deal["id"], version=saved["version"])
    assert edited["deal_so_far"] == saved["deal_so_far"]
    assert edited["deal_so_far_updated_at"] == saved["deal_so_far_updated_at"]
    login = await client.get("/login")
    csrf = re.search(r'name="csrf" value="([^"]+)"', login.text)[1]
    await client.post("/auth/dev", data={"csrf": csrf})
    page = await client.get(f"/deals/{deal['id']}")
    assert "<h2>Status</h2>" in page.text
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text)[1]
    conflict = await client.post(
        f"/deals/{deal['id']}/status",
        data={
            "csrf": csrf,
            "version": str(saved["version"]),
            "key": "conflict-draft",
            "deal_so_far": "Keep my unsaved draft",
        },
    )
    assert conflict.status_code == 409
    assert "Keep my unsaved draft" in conflict.text
    clear = await client.post(
        f"/deals/{deal['id']}/status",
        data={"csrf": csrf, "version": str(edited["version"]), "key": "clear-summary", "deal_so_far": ""},
    )
    assert clear.status_code == 303
    assert (await crm.get(human, deal["id"]))["deal_so_far"] == ""
