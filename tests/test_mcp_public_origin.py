import re
from urllib.parse import urlsplit

import pytest
from test_local_auth import ADMIN, PASSWORD, password_login, token
from test_oauth_mcp import authorize


@pytest.mark.parametrize(
    "system", ["https://crm.example.test", "https://crm.example.test:8443"], indirect=True
)
async def test_public_origin_oauth_and_mcp_reject_untrusted_hosts(system):
    app, client = system
    origin = str(client.base_url).rstrip("/")
    auth = app.state.local_auth
    await auth.activate(token(await auth.issue(ADMIN)), PASSWORD, "127.0.0.1")
    assert (await password_login(client)).status_code == 303
    page = await client.get("/settings")
    csrf = re.search(r'name="csrf-token" content="([^"]+)"', page.text)[1]
    _, tokens = await authorize(client, csrf)
    headers = {
        "Authorization": "Bearer " + tokens["access_token"],
        "Accept": "application/json, text/event-stream",
        "Origin": origin,
    }
    initialized = await client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "public-origin-test", "version": "1"},
            },
        },
    )
    assert initialized.status_code == 200, initialized.text
    assert initialized.json()["result"]["serverInfo"]["name"] == "Overdrive"
    # Native clients do not send Origin; the configured public Host must still work.
    headers.pop("Origin")
    request = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    listed = await client.post("/mcp", headers=headers, json=request)
    assert listed.status_code == 200, listed.text
    assert "whoami" in {tool["name"] for tool in listed.json()["result"]["tools"]}
    for host in ["attacker.example", "localhost:18765", urlsplit(origin).netloc + ".attacker.example"]:
        denied = await client.post("/mcp", headers=headers | {"Host": host}, json=request)
        assert denied.status_code == 421
    denied = await client.post("/mcp", headers=headers | {"Origin": "https://attacker.example"}, json=request)
    assert denied.status_code == 403
    unauthenticated = await client.post("/mcp", json=request)
    assert unauthenticated.status_code == 401
