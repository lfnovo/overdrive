import asyncio
import re
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from test_oauth_mcp import authorize, login

from overdrive.database import ensure_record_id
from overdrive.service import Actor, DomainError

PASSWORD = "a long and memorable passphrase"
ADMIN = "user:initial_admin"


def csrf(page):
    return re.search(r'name="csrf" value="([^"]+)"', page.text)[1]


def token(link):
    return parse_qs(urlsplit(link).query)["token"][0]


async def password_login(c, email="admin@example.test", password=PASSWORD):
    page = await c.get("/login")
    return await c.post("/auth/password", data={"csrf": csrf(page), "email": email, "password": password})


async def test_activation_login_and_mcp_reset_revocation(system):
    app, c = system
    auth = app.state.local_auth
    link = await auth.issue(ADMIN)
    page = await c.get(link)
    assert page.status_code == 200
    assert page.headers["referrer-policy"] == "no-referrer"
    assert page.headers["cache-control"] == "no-store"
    assert (await c.get("/api/deal")).status_code == 401
    result = await c.post(
        "/auth/activate",
        data={"csrf": csrf(page), "token": token(link), "password": PASSWORD, "confirm": PASSWORD},
    )
    assert result.status_code == 200 and "Your keys are ready" in result.text
    assert (await c.get("/api/deal")).status_code == 401  # activation does not sign in
    cred = await auth.credential(ADMIN)
    assert cred["password_hash"].startswith("$argon2id$")
    assert PASSWORD not in str(cred) and token(link) not in str(cred)
    assert (await c.get(link)).status_code == 400
    assert (await password_login(c, email=" ADMIN@example.test ")).status_code == 303
    settings = await c.get("/settings")
    current_csrf = re.search(r'name="csrf-token" content="([^"]+)"', settings.text)[1]
    client_id, tokens = await authorize(c, current_csrf)
    deal = await app.state.crm.save(Actor(ADMIN), "deal", {"title": "Auth test"})
    transfer = await app.state.provider.file_ticket(tokens["access_token"], deal["id"])
    assert (
        await c.get("/api/deal", headers={"Authorization": "Bearer " + tokens["access_token"]})
    ).status_code == 200
    assert "password_hash" not in (await c.get("/api/user")).text
    assert (await c.get("/api/credential")).status_code in {400, 404}
    cookie = c.cookies.get("overdrive_session")
    renewed = await auth.issue(ADMIN)
    c.cookies.set("overdrive_session", cookie)
    assert (await c.get("/api/deal")).status_code == 401
    assert (await password_login(c)).status_code == 400
    assert (
        await c.get("/api/deal", headers={"Authorization": "Bearer " + tokens["access_token"]})
    ).status_code == 401
    assert (
        await c.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": tokens["refresh_token"],
            },
        )
    ).status_code == 400
    with pytest.raises(DomainError):
        await app.state.provider.use_file_ticket(
            token(transfer["url"])
            if "token=" in transfer["url"]
            else parse_qs(urlsplit(transfer["url"]).query)["ticket"][0],
            "/api/deals/" + deal["id"] + "/files",
            "POST",
        )
    await auth.activate(token(renewed), PASSWORD, "other-ip")
    assert (await password_login(c)).status_code == 303


async def test_activation_expiry_replacement_and_concurrent_consumption(system):
    app, _ = system
    auth = app.state.local_auth
    first = await auth.issue(ADMIN)
    second = await auth.issue(ADMIN)
    with pytest.raises(DomainError):
        await auth.activate(token(first), PASSWORD, "ip")
    await app.state.db.query(
        "UPDATE $id SET activation_expires = $expired;",
        {"id": auth.credential_id(ADMIN), "expired": int(time.time()) - 1},
    )
    with pytest.raises(DomainError):
        await auth.activate(token(second), PASSWORD, "ip")
    link = await auth.issue(ADMIN)
    results = await asyncio.gather(
        auth.activate(token(link), PASSWORD, "ip"),
        auth.activate(token(link), PASSWORD, "ip"),
        return_exceptions=True,
    )
    assert sum(isinstance(r, tuple) for r in results) == 1
    assert sum(isinstance(r, DomainError) for r in results) == 1


async def test_admin_only_generation_csrf_and_disabled_users(system):
    app, c = system
    auth = app.state.local_auth
    await login(c)
    member = await app.state.crm.save(
        Actor(ADMIN),
        "user",
        {"name": "Member", "email": "member@example.test", "admin": False, "active": True, "units": []},
    )
    url = "/settings/users/" + member["id"] + "/activation"
    assert (await c.post(url)).status_code == 403
    page = await c.get(url)
    generated = await c.post(url, data={"csrf": csrf(page)})
    assert generated.status_code == 200 and "Activation link" in generated.text
    link = await auth.issue(member["id"])
    await auth.activate(token(link), PASSWORD, "ip")
    await password_login(c, member["email"])
    assert (await c.get("/settings/users/" + ADMIN + "/activation")).status_code == 403
    page = await c.get("/account/password")
    assert (
        await c.post("/settings/users/" + ADMIN + "/activation", data={"csrf": csrf(page)})
    ).status_code == 403
    pending = await auth.issue(member["id"])
    await app.state.db.query("UPDATE $id SET active = false;", {"id": ensure_record_id(member["id"])})
    with pytest.raises(DomainError):
        await auth.activate(token(pending), PASSWORD, "ip")
    assert (await password_login(c, member["email"])).status_code == 400


async def test_password_change_invalidates_old_sessions_and_limits_attempts(system):
    app, c = system
    auth = app.state.local_auth
    await auth.activate(token(await auth.issue(ADMIN)), PASSWORD, "ip")
    assert (await password_login(c)).status_code == 303
    before = await auth.epoch(ADMIN)
    page = await c.get("/account/password")
    response = await c.post(
        "/account/password",
        data={
            "csrf": csrf(page),
            "current": PASSWORD,
            "password": PASSWORD + " changed",
            "confirm": PASSWORD + " changed",
        },
    )
    assert response.status_code == 303
    assert await auth.epoch(ADMIN) != before
    assert (await c.get("/settings")).status_code == 200
    assert (await password_login(c)).status_code == 400
    assert (await password_login(c, password=PASSWORD + " changed")).status_code == 303
    for _ in range(10):
        with pytest.raises(DomainError, match="Email or password"):
            await auth.authenticate("missing@example.test", "wrong", "test-ip")
    with pytest.raises(DomainError) as error:
        await auth.authenticate("missing@example.test", "wrong", "test-ip")
    assert error.value.status == 429
    # Limits persist in the database, not in a particular service instance.
    from overdrive.local_auth import LocalAuth

    with pytest.raises(DomainError) as error:
        await LocalAuth(app.state.db, app.state.crm, app.state.settings).authenticate(
            "missing@example.test", "wrong", "another-ip"
        )
    assert error.value.status == 429


async def test_local_disabled_google_guard_and_password_validation(system, monkeypatch):
    app, c = system
    auth = app.state.local_auth
    link = await auth.issue(ADMIN)
    with pytest.raises(DomainError, match="15 and 128"):
        await auth.activate(token(link), "short", "ip")
    assert (
        await c.post("/auth/password", data={"email": "admin@example.test", "password": PASSWORD})
    ).status_code == 403
    app.state.settings.local_login = False
    assert (await password_login(c)).status_code == 404
    assert (await c.get(link)).status_code == 404
    app.state.settings.google_login = False
    assert (await c.get("/auth/google")).status_code == 503
    assert (await c.get("/auth/google/callback")).status_code == 503
    app.state.settings.google_login = True
    app.state.settings.google_client_id = "mock-client"
    app.state.settings.google_client_secret = "mock-secret"

    async def verified(request):
        return {"userinfo": {"sub": "google-test", "email": "admin@example.test", "email_verified": True}}

    monkeypatch.setattr(app.state.oauth.google, "authorize_access_token", verified)
    assert (await c.get("/auth/google/callback")).status_code == 303
    assert (await c.get("/settings")).status_code == 200

    async def unverified(request):
        return {"userinfo": {"sub": "intruder", "email": "admin@example.test", "email_verified": False}}

    monkeypatch.setattr(app.state.oauth.google, "authorize_access_token", unverified)
    assert (await c.get("/auth/google/callback")).headers["location"].startswith("/login")


async def test_logout_revokes_cookie_and_reset_invalidates_pending_code(system):
    from mcp.server.auth.provider import AuthorizationCode, TokenError
    from mcp.shared.auth import OAuthClientInformationFull

    app, c = system
    await login(c)
    page = await c.get("/settings")
    old_cookie = c.cookies.get("overdrive_session")
    assert (await c.post("/logout", data={"csrf": csrf(page)})).status_code == 303
    c.cookies.clear()
    c.cookies.set("overdrive_session", old_cookie)
    assert (await c.get("/api/deal")).status_code == 401
    provider = app.state.provider
    client = OAuthClientInformationFull(client_id="test-client", redirect_uris=["http://localhost/callback"])
    code = AuthorizationCode(
        code="pending-code",
        scopes=["crm"],
        expires_at=time.time() + 120,
        client_id="test-client",
        code_challenge="x",
        redirect_uri="http://localhost/callback",
        redirect_uri_provided_explicitly=True,
        resource=app.state.settings.app_url + "/mcp",
        subject=ADMIN,
    )
    await provider.put(
        code.code, "code", code.model_dump(mode="json") | {"auth_epoch": await provider.epoch(ADMIN)}, 120
    )
    await app.state.local_auth.issue(ADMIN)
    with pytest.raises(TokenError):
        await provider.exchange_authorization_code(client, code)
