"""Live verification against the local app using its explicit development login."""

import asyncio
import base64
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from overdrive.config import Settings


async def main():
    s = Settings()
    assert s.dev_login and not s.secure, "Only the explicit local development login is supported."
    async with httpx.AsyncClient(base_url=s.app_url, follow_redirects=False) as web:
        page = await web.get("/login")
        page.raise_for_status()
        csrf = re.search(r'name="csrf" value="([^"]+)"', page.text)[1]
        assert (await web.post("/auth/dev", data={"csrf": csrf})).status_code == 303
        page = await web.get("/settings")
        csrf = re.search(r'name="csrf-token" content="([^"]+)"', page.text)[1]
        reg = await web.post(
            "/register",
            json={
                "client_name": "Local Python SDK verification",
                "redirect_uris": ["http://localhost:18766/callback"],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "scope": "crm",
            },
        )
        reg.raise_for_status()
        client_id = reg.json()["client_id"]
        verifier = "local-verification-" + "a" * 64
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        auth = await web.get(
            "/authorize",
            params={
                "client_id": client_id,
                "response_type": "code",
                "redirect_uri": "http://localhost:18766/callback",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": "crm",
                "state": "local-verification",
                "resource": s.app_url + "/mcp",
            },
        )
        consent = await web.get(auth.headers["location"])
        consent.raise_for_status()
        ticket = re.search(r'name="ticket" value="([^"]+)"', consent.text)[1]
        accepted = await web.post(
            "/oauth/consent", data={"ticket": ticket, "csrf": csrf, "decision": "allow"}
        )
        params = parse_qs(urlparse(accepted.headers["location"]).query)
        assert params["state"] == ["local-verification"]
        token = await web.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": params["code"][0],
                "redirect_uri": "http://localhost:18766/callback",
                "code_verifier": verifier,
            },
        )
        token.raise_for_status()
        access = token.json()["access_token"]
        report = {
            "endpoint": s.app_url + "/mcp",
            "identity": "explicit development login",
            "oauth": "authorization_code + S256 PKCE",
        }
        try:
            async with (
                httpx.AsyncClient(headers={"Authorization": "Bearer " + access}) as http,
                streamable_http_client(s.app_url + "/mcp", http_client=http) as (read, write, _),
                ClientSession(read, write) as session,
            ):
                init = await session.initialize()
                tools = await session.list_tools()
                result = await session.call_tool("whoami", {})
                assert not result.isError
                report["python_sdk"] = {
                    "server": init.serverInfo.name,
                    "tools": [t.name for t in tools.tools],
                    "whoami_ok": True,
                }
            print(json.dumps(report, indent=2))
        finally:
            revoked = await web.post(
                "/revoke", data={"token": access, "client_id": client_id, "token_type_hint": "access_token"}
            )
            report["temporary_token_revoked"] = revoked.status_code == 200
            Path("docs/evidence/live-mcp.json").write_text(json.dumps(report, indent=2) + "\n")


asyncio.run(main())
