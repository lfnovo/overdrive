import base64
import hashlib
import re
from urllib.parse import parse_qs, urlparse


async def login(client):
    page = await client.get("/login")
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text)[1]
    await client.post("/auth/dev", data={"csrf": csrf})
    page = await client.get("/settings")
    return re.search(r'name="csrf-token" content="([^"]+)"', page.text)[1]


async def authorize(client, csrf, name="Test agent"):
    reg = await client.post(
        "/register",
        json={
            "client_name": name,
            "redirect_uris": ["http://localhost:8765/callback"],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "scope": "crm",
        },
    )
    assert reg.status_code == 201, reg.text
    identity = reg.json()["client_id"]
    verifier = "a" * 64
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    response = await client.get(
        "/authorize",
        params={
            "client_id": identity,
            "response_type": "code",
            "redirect_uri": "http://localhost:8765/callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": "crm",
            "state": "client-state",
            "resource": str(client.base_url).rstrip("/") + "/mcp",
        },
    )
    assert response.status_code == 302, response.text
    consent = await client.get(response.headers["location"])
    assert consent.status_code == 200, consent.text
    ticket = re.search(r'name="ticket" value="([^"]+)"', consent.text)[1]
    granted = await client.post("/oauth/consent", data={"csrf": csrf, "ticket": ticket, "decision": "allow"})
    assert granted.status_code == 303, granted.text
    params = parse_qs(urlparse(granted.headers["location"]).query)
    assert params["state"] == ["client-state"]
    form = {
        "grant_type": "authorization_code",
        "client_id": identity,
        "code": params["code"][0],
        "redirect_uri": "http://localhost:8765/callback",
        "code_verifier": verifier,
    }
    invalid = await client.post("/token", data=form | {"code_verifier": "wrong" * 13})
    assert invalid.status_code == 400, invalid.text
    token = await client.post("/token", data=form)
    assert token.status_code == 200, token.text
    replay = await client.post("/token", data=form)
    assert replay.status_code == 400, replay.text
    return identity, token.json()


async def test_real_oauth_routes_and_mcp_transport(system):
    app, c = system
    csrf = await login(c)
    client_id, token = await authorize(c, csrf)
    headers = {
        "Authorization": "Bearer " + token["access_token"],
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-11-25",
    }
    init = await c.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "integration-client", "version": "1"},
            },
        },
    )
    assert init.status_code == 200, init.text
    assert init.json()["result"]["serverInfo"]["name"] == "Overdrive"
    listed = await c.post(
        "/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    )
    names = {t["name"] for t in listed.json()["result"]["tools"]}
    assert not {"add_proposal", "proposal_status"} & names
    assert {"create_deal", "create_task", "my_tasks", "add_link", "attachment_transfer"} <= names
    created = await c.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "create_deal",
                "arguments": {"data": {"title": "Via MCP"}, "idempotency_key": "mcp1"},
            },
        },
    )
    assert not created.json()["result"].get("isError"), created.text
    api = await c.get("/api/deal", headers=headers)
    assert any(d["title"] == "Via MCP" for d in api.json()), api.text
    deal_id = next(d["id"] for d in api.json() if d["title"] == "Via MCP")
    linked = await c.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "add_link",
                "arguments": {
                    "data": {
                        "deal": deal_id,
                        "title": "Material MCP",
                        "url": "https://example.test/material",
                    },
                    "idempotency_key": "mcp-link",
                },
            },
        },
    )
    assert not linked.json()["result"].get("isError"), linked.text
    items = await c.get("/api/attachment", params={"deal": deal_id}, headers=headers)
    material = items.json()[0]
    updated = await c.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {
                "name": "update_record",
                "arguments": {
                    "record_id": material["id"],
                    "expected_version": material["version"],
                    "changes": {"deprecated": True},
                    "idempotency_key": "mcp-stale",
                },
            },
        },
    )
    assert not updated.json()["result"].get("isError"), updated.text
    items = await c.get("/api/attachment", params={"deal": deal_id, "deprecated": False}, headers=headers)
    assert items.json() == []
    deleted = await c.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "delete_attachment",
                "arguments": {
                    "attachment_id": material["id"],
                    "expected_version": 2,
                    "idempotency_key": "mcp-delete",
                },
            },
        },
    )
    assert not deleted.json()["result"].get("isError"), deleted.text
    assert (await c.get("/api/attachment/" + material["id"], headers=headers)).status_code == 404
    refresh = await c.post(
        "/token",
        data={"grant_type": "refresh_token", "client_id": client_id, "refresh_token": token["refresh_token"]},
    )
    assert refresh.status_code == 200, refresh.text
    old_refresh = await c.post(
        "/token",
        data={"grant_type": "refresh_token", "client_id": client_id, "refresh_token": token["refresh_token"]},
    )
    assert old_refresh.status_code == 400, old_refresh.text
    # UI revocation invalidates original and renewed access tokens in this grant.
    connections = await app.state.provider.connections(
        __import__("overdrive.service", fromlist=["Actor"]).Actor("user:initial_admin")
    )
    revoke = await c.post("/connections/" + connections[0]["id"] + "/revoke", data={"csrf": csrf})
    assert revoke.status_code == 303
    for access in [token["access_token"], refresh.json()["access_token"]]:
        denied = await c.post(
            "/mcp",
            headers=headers | {"Authorization": "Bearer " + access},
            json={"jsonrpc": "2.0", "id": 4, "method": "tools/list"},
        )
        assert denied.status_code == 401, denied.text
