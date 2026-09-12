"""Local credentials are separate from CRM records and never exposed by CRUD/MCP."""

import asyncio
import secrets
import time

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from .auth import hashed
from .database import ensure_record_id
from .service import Actor, DomainError, now

HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=1)
DUMMY_HASH = HASHER.hash(secrets.token_urlsafe(32))


async def initial_admin(db, settings):
    if settings.admin_email and not await db.rows("SELECT VALUE id FROM user LIMIT 1;"):
        await db.query(
            "CREATE user:initial_admin CONTENT $data;",
            {
                "data": {
                    "name": settings.admin_name,
                    "email": settings.admin_email.strip().lower(),
                    "active": True,
                    "admin": True,
                    "units": [],
                    "version": 1,
                    "created_at": now(),
                    "updated_at": now(),
                }
            },
        )


class LocalAuth:
    def __init__(self, db, crm, settings):
        self.db, self.crm, self.settings = db, crm, settings
        self.hash_slots = asyncio.Semaphore(4)

    def require_enabled(self):
        if not self.settings.local_login:
            raise DomainError("Password sign-in is disabled for this workspace.", 404)

    def credential_id(self, subject):
        return ensure_record_id("credential:" + hashed(str(ensure_record_id(subject, "user"))))

    async def credential(self, subject):
        return await self.db.get(str(self.credential_id(subject))) or {}

    async def epoch(self, subject):
        return (await self.credential(subject)).get("epoch", "")

    async def throttle(self, ip, identity="", *, purpose="login"):
        # Shared across workers/restarts; never trust client-supplied forwarding headers.
        stamp = int(time.time())
        for key, limit in [(purpose + ":ip:" + ip, 60), (purpose + ":account:" + identity, 10)]:
            if key.endswith(":account:"):
                continue
            rows = await self.db.rows(
                "UPSERT $id SET attempts = IF expires_at = NONE OR expires_at <= $now "
                "THEN 1 ELSE attempts + 1 END, expires_at = IF expires_at = NONE OR expires_at <= $now "
                "THEN $until ELSE expires_at END RETURN AFTER;",
                {"id": ensure_record_id("auth_throttle:" + hashed(key)), "now": stamp, "until": stamp + 900},
            )
            if rows[0]["attempts"] > limit:
                raise DomainError("Too many attempts. Try again in 15 minutes.", 429)
        await self.db.query("DELETE auth_throttle WHERE expires_at <= $now;", {"now": stamp})

    async def verify(self, password, encoded):
        async with self.hash_slots:
            try:
                return await asyncio.to_thread(HASHER.verify, encoded or DUMMY_HASH, password)
            except (VerificationError, InvalidHashError):
                return False

    async def encode(self, password):
        if not 15 <= len(password) <= 128:
            raise DomainError("Use a password between 15 and 128 characters. Spaces are welcome.")
        async with self.hash_slots:
            return await asyncio.to_thread(HASHER.hash, password)

    async def authenticate(self, email, password, ip):
        self.require_enabled()
        email = email.strip().lower()[:320]
        await self.throttle(ip, email)
        rows = await self.db.rows("SELECT * FROM user WHERE email = $email;", {"email": email})
        user = rows[0] if rows else None
        cred = await self.credential(user["id"]) if user else {}
        valid = await self.verify(password[:129], cred.get("password_hash"))
        if not valid or not user or not user["active"] or not cred.get("password_hash"):
            raise DomainError("Email or password is incorrect.", 400)
        # Reset while hashing must not yield a session with the new epoch.
        if cred.get("epoch") != await self.epoch(user["id"]):
            raise DomainError("Email or password is incorrect.", 400)
        return user, cred["epoch"]

    async def issue(self, subject):
        """Call only after admin authorization (or from the server operator CLI)."""
        self.require_enabled()
        await self.crm.user(Actor(subject))
        token = secrets.token_urlsafe(32)
        await self.db.query(
            "UPSERT $id SET subject = $subject, password_hash = '', epoch = $epoch, "
            "activation_hash = $hash, activation_expires = $expires;",
            {
                "id": self.credential_id(subject),
                "subject": ensure_record_id(subject, "user"),
                "epoch": secrets.token_hex(16),
                "hash": hashed(token),
                "expires": int(time.time()) + 86400,
            },
        )
        return self.settings.app_url.rstrip("/") + "/auth/activate?token=" + token

    async def activation(self, token):
        self.require_enabled()
        if not 20 <= len(token) <= 128:
            raise DomainError(
                "This activation link is invalid or expired. Ask your administrator for a new one."
            )
        rows = await self.db.rows(
            "SELECT * FROM credential WHERE activation_hash = $hash AND activation_expires > $now;",
            {"hash": hashed(token), "now": int(time.time())},
        )
        if not rows:
            raise DomainError(
                "This activation link is invalid or expired. Ask your administrator for a new one."
            )
        await self.crm.user(Actor(rows[0]["subject"]))
        return rows[0]

    async def activate(self, token, password, ip):
        await self.throttle(ip, purpose="activate")
        cred = await self.activation(token)
        encoded = await self.encode(password)
        epoch = secrets.token_hex(16)
        # Single conditional write consumes the link, even across processes.
        rows = await self.db.rows(
            "UPDATE $id SET password_hash = $password, epoch = $epoch, activation_hash = '', "
            "activation_expires = 0 WHERE activation_hash = $hash AND activation_expires > $now "
            "AND subject.active = true RETURN AFTER;",
            {
                "id": ensure_record_id(cred["id"]),
                "password": encoded,
                "epoch": epoch,
                "hash": hashed(token),
                "now": int(time.time()),
            },
        )
        if not rows:
            raise DomainError(
                "This activation link is invalid or expired. Ask your administrator for a new one."
            )
        return cred["subject"], epoch

    async def change_password(self, subject, current, password, ip):
        self.require_enabled()
        await self.throttle(ip, subject, purpose="change")
        cred = await self.credential(subject)
        if not cred.get("password_hash") or not await self.verify(current[:129], cred["password_hash"]):
            raise DomainError("Current password is incorrect.")
        encoded = await self.encode(password)
        epoch = secrets.token_hex(16)
        rows = await self.db.rows(
            "UPDATE $id SET password_hash = $password, epoch = $epoch, activation_hash = '', "
            "activation_expires = 0 WHERE epoch = $before AND subject.active = true RETURN AFTER;",
            {"id": self.credential_id(subject), "password": encoded, "epoch": epoch, "before": cred["epoch"]},
        )
        if not rows:
            raise DomainError("Your credentials changed. Sign in again.", 401)
        return epoch
