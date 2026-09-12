import hashlib
import secrets
import time
from urllib.parse import urlencode

from mcp.server.auth.provider import AccessToken, AuthorizationCode, AuthorizeError, RefreshToken, TokenError
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from .database import ensure_record_id
from .service import Actor, DomainError, now


def hashed(value):
    return hashlib.sha256(value.encode()).hexdigest()


class OAuthProvider:
    """SDK handles PKCE, metadata, client validation and protocol endpoints.

    Store persists opaque token hashes and their grants in SurrealDB.
    """

    def __init__(self, db, crm, settings):
        self.db, self.crm, self.settings = db, crm, settings

    async def epoch(self, subject):
        row = await self.db.get("credential:" + hashed(subject))
        return (row or {}).get("epoch", "")

    async def put(self, key, kind, payload, ttl=3600):
        stamp = now()
        await self.db.query(
            "UPSERT $id CONTENT $data;",
            {
                "id": ensure_record_id("auth_record:" + hashed(key)),
                "data": {
                    "kind": kind,
                    "payload": payload,
                    "expires_at": int(time.time()) + ttl,
                    "version": 1,
                    "created_at": stamp,
                    "updated_at": stamp,
                },
            },
        )

    async def load(self, key, kind=None):
        row = await self.db.get("auth_record:" + hashed(key))
        if not row or row["expires_at"] <= time.time() or (kind and row["kind"] != kind):
            return None
        return row["payload"]

    async def delete(self, key):
        await self.db.query("DELETE $id;", {"id": ensure_record_id("auth_record:" + hashed(key))})

    async def get_client(self, client_id):
        p = await self.load("client:" + client_id, "client")
        return OAuthClientInformationFull.model_validate(p) if p else None

    async def register_client(self, client_info):
        await self.put(
            "client:" + client_info.client_id, "client", client_info.model_dump(mode="json"), 365 * 86400
        )

    async def authorize(self, client, params):
        resource = params.resource or self.settings.app_url + "/mcp"
        if resource.rstrip("/") != (self.settings.app_url + "/mcp").rstrip("/"):
            raise AuthorizeError(error="invalid_target", error_description="Invalid resource")
        ticket = secrets.token_urlsafe(32)
        await self.put(
            ticket, "pending", {"client_id": client.client_id, "params": params.model_dump(mode="json")}, 600
        )
        return self.settings.app_url + "/oauth/consent?ticket=" + ticket

    async def grant(self, actor, ticket, auth_epoch=None):
        async with self.db.lock:
            await self.crm.user(actor)
            epoch = await self.epoch(actor.id) if auth_epoch is None else auth_epoch
            if epoch != await self.epoch(actor.id):
                raise DomainError("Your session expired. Sign in again.", 401)
            pending = await self.load(ticket, "pending")
            if not pending:
                raise DomainError("Authorization expired.", 400)
            await self.delete(ticket)
            code = secrets.token_urlsafe(40)
            p = pending["params"]
            record = AuthorizationCode(
                code=code,
                scopes=p["scopes"] or ["crm"],
                expires_at=time.time() + 120,
                client_id=pending["client_id"],
                code_challenge=p["code_challenge"],
                redirect_uri=p["redirect_uri"],
                redirect_uri_provided_explicitly=p["redirect_uri_provided_explicitly"],
                resource=self.settings.app_url + "/mcp",
                subject=actor.id,
            )
            await self.put(
                code, "code", record.model_dump(mode="json") | {"code": "", "auth_epoch": epoch}, 120
            )
            query = {"code": code}
            if p["state"] is not None:
                query["state"] = p["state"]
            return p["redirect_uri"] + ("&" if "?" in p["redirect_uri"] else "?") + urlencode(query)

    async def load_authorization_code(self, client, authorization_code):
        data = await self.load(authorization_code, "code")
        if not data or data["client_id"] != client.client_id:
            return None
        return AuthorizationCode.model_validate(data | {"code": authorization_code})

    async def _tokens(self, subject, client_id, scopes, resource, grant=None, auth_epoch=None):
        await self.crm.user(Actor(subject))
        epoch = await self.epoch(subject) if auth_epoch is None else auth_epoch
        if epoch != await self.epoch(subject):
            raise TokenError(error="invalid_grant")
        grant = grant or secrets.token_urlsafe(32)
        old = await self.load(grant, "grant")
        await self.put(
            grant,
            "grant",
            {
                "subject": subject,
                "client_id": client_id,
                "created_at": (old or {}).get("created_at", now()),
                "last_used": now(),
                "revoked": False,
                "auth_epoch": epoch,
                "generation": (old or {}).get("generation", 0) + 1,
            },
            30 * 86400,
        )
        access, refresh = secrets.token_urlsafe(40), secrets.token_urlsafe(40)
        common = {
            "client_id": client_id,
            "scopes": scopes,
            "resource": resource,
            "subject": subject,
            "grant": grant,
            "auth_epoch": epoch,
            "generation": (old or {}).get("generation", 0) + 1,
        }
        await self.put(access, "access", common | {"expires_at": int(time.time()) + 3600}, 3600)
        await self.put(refresh, "refresh", common | {"expires_at": int(time.time()) + 30 * 86400}, 30 * 86400)
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=3600,
            refresh_token=refresh,
            scope=" ".join(scopes),
        )

    async def exchange_authorization_code(self, client, authorization_code):
        async with self.db.lock:
            data = await self.load(authorization_code.code, "code")
            if (
                not data
                or data["client_id"] != client.client_id
                or data.get("auth_epoch", "") != await self.epoch(data["subject"])
            ):
                raise TokenError(error="invalid_grant")
            await self.delete(authorization_code.code)
            return await self._tokens(
                data["subject"],
                client.client_id,
                data["scopes"],
                data["resource"],
                auth_epoch=data.get("auth_epoch", ""),
            )

    async def _valid(self, token, kind):
        data = await self.load(token, kind)
        if not data:
            return None
        grant = await self.load(data["grant"], "grant")
        if (
            not grant
            or grant["revoked"]
            or data.get("generation") != grant.get("generation")
            or grant.get("auth_epoch", "") != await self.epoch(data["subject"])
        ):
            return None
        try:
            await self.crm.user(Actor(data["subject"]))
        except DomainError:
            return None
        return data

    async def load_refresh_token(self, client, refresh_token):
        data = await self._valid(refresh_token, "refresh")
        if not data or data["client_id"] != client.client_id:
            return None
        return RefreshToken.model_validate({**data, "token": refresh_token})

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        async with self.db.lock:
            data = await self._valid(refresh_token.token, "refresh")
            if not data or data["client_id"] != client.client_id:
                raise TokenError(error="invalid_grant")
            await self.delete(refresh_token.token)
            return await self._tokens(
                data["subject"],
                client.client_id,
                scopes,
                data["resource"],
                data["grant"],
                auth_epoch=data.get("auth_epoch", ""),
            )

    async def load_access_token(self, token):
        data = await self._valid(token, "access")
        if not data:
            return None
        return AccessToken.model_validate({**data, "token": token})

    async def revoke_token(self, token):
        data = await self.load(token.token)
        if data and data.get("grant"):
            await self.revoke_grant(data["grant"])

    async def revoke_grant(self, grant):
        p = await self.load(grant, "grant")
        if p:
            await self.put(grant, "grant", p | {"revoked": True}, 30 * 86400)

    async def file_ticket(self, token, deal, attachment=None):
        data = await self._valid(token, "access")
        if not data:
            raise DomainError("Connection expired.", 401)
        actor = Actor(data["subject"], "mcp", data["client_id"])
        await self.crm.get(actor, deal, "deal")
        if attachment:
            record = await self.crm.get(actor, attachment, "attachment")
            if not record.get("storage_key"):
                raise DomainError("This item is a link, not a file.")
            if record["deal"] != deal:
                raise DomainError("File not found.", 404)
        ticket = secrets.token_urlsafe(40)
        await self.put(
            ticket,
            "file_ticket",
            {
                "subject": actor.id,
                "client_id": actor.client,
                "grant": data["grant"],
                "deal": deal,
                "attachment": attachment,
            },
            300,
        )
        path = "/files/" + attachment if attachment else "/api/deals/" + deal + "/files"
        return {
            "url": self.settings.app_url + path + "?ticket=" + ticket,
            "method": "GET" if attachment else "POST",
            "expires_in": 300,
            "field": None if attachment else "file",
            "max_bytes": self.settings.max_upload_bytes,
        }

    async def use_file_ticket(self, ticket, path, method):
        async with self.db.lock:
            data = await self.load(ticket, "file_ticket")
            if not data:
                raise DomainError("Invalid or expired link.", 401)
            expected = (
                "/files/" + data["attachment"]
                if data.get("attachment")
                else "/api/deals/" + data["deal"] + "/files"
            )
            if path != expected or method != ("GET" if data.get("attachment") else "POST"):
                raise DomainError("This link cannot be used for this operation.", 403)
            grant = await self.load(data["grant"], "grant")
            if (
                not grant
                or grant["revoked"]
                or grant.get("auth_epoch", "") != await self.epoch(data["subject"])
            ):
                raise DomainError("Connection revoked.", 401)
            actor = Actor(data["subject"], "mcp", data["client_id"])
            await self.crm.get(actor, data["deal"], "deal")
            if method == "POST":
                await self.delete(ticket)
            return actor

    async def connections(self, actor):
        await self.crm.user(actor)
        rows = await self.db.rows(
            "SELECT id, payload, expires_at FROM auth_record WHERE kind = 'grant' AND payload.subject = $user AND expires_at > $time;",
            {"user": actor.id, "time": int(time.time())},
        )
        epoch = await self.epoch(actor.id)
        for row in rows:
            if row["payload"].get("auth_epoch", "") != epoch:
                row["payload"]["revoked"] = True
            client = await self.get_client(row["payload"]["client_id"])
            row["name"] = client.client_name if client else row["payload"]["client_id"]
        return rows
