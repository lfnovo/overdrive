import asyncio
import re
from urllib.parse import urlparse

import surreal_basics
from surreal_basics import get_async_connection, reset_connections_async
from surreal_basics.exceptions import SurrealDBError
from surrealdb import RecordID
from surrealdb.errors import SurrealError


class DatabaseError(Exception):
    pass


def ensure_record_id(value: str | RecordID, table: str | None = None) -> RecordID:
    text = str(value)
    if not re.fullmatch(r"[a-z_]+:[A-Za-z0-9_]+", text):
        raise ValueError("Invalid identifier")
    name, key = text.split(":", 1)
    if table and name != table:
        raise ValueError("Invalid identifier type")
    return RecordID(name, key)


def serial(value):
    if isinstance(value, RecordID):
        return str(value)
    if isinstance(value, dict):
        return {k: serial(v) for k, v in value.items()}
    if isinstance(value, list):
        return [serial(v) for v in value]
    return value


class Database:
    """Overdrive result/transaction adapter over surreal-basics connections.

    surreal-basics config is process-global. Only one Database may own it at a
    time; never silently retarget an active application's queries.
    """

    _active = None

    def __init__(self, settings):
        self.settings = settings
        self.lock = asyncio.Lock()
        self.io_lock = asyncio.Lock()

    async def connect(self):
        if Database._active is self:
            return
        if Database._active is not None:
            raise RuntimeError("A database is already active in this process")
        url = urlparse(self.settings.surreal_url)
        if (
            url.scheme not in {"http", "https", "ws", "wss"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in {"", "/", "/rpc"}
        ):
            raise ValueError("Use an HTTP(S) or WS(S) SurrealDB URL, without credentials or a custom path")
        self.configuration = {
            "host": url.hostname,
            "port": url.port or (443 if url.scheme in {"https", "wss"} else 80),
            "mode": "ws" if url.scheme in {"ws", "wss"} else "http",
            "tls": url.scheme in {"https", "wss"},
            "user": self.settings.surreal_user,
            "password": self.settings.surreal_pass,
            "namespace": self.settings.surreal_namespace,
            "database": self.settings.surreal_database,
            "auth_scope": "database",
            "persistent": True,
        }
        Database._active = self
        try:
            await reset_connections_async()
            surreal_basics.init(**self.configuration)
            await self.rows("RETURN 1;")
        except BaseException:
            await self.close()
            raise

    def check_target(self):
        if Database._active is not self:
            raise RuntimeError("Database is not connected")
        config = surreal_basics.get_config()
        if any(getattr(config, key) != value for key, value in self.configuration.items()):
            raise RuntimeError("surreal-basics configuration changed while the database was active")

    async def close(self):
        if Database._active is self:
            try:
                await reset_connections_async()
            finally:
                Database._active = None

    async def query(self, sql, params=None):
        self.check_target()
        # Keep every statement's result: repo_query validates all statements
        # but returns only the first. Do not retry writes after ambiguous failures.
        async with self.io_lock:
            try:
                async with get_async_connection() as connection:
                    raw = await connection.query_raw(sql, params or {})
            except (SurrealDBError, SurrealError) as error:
                raise DatabaseError(str(error)) from error
        if "error" in raw:
            raise DatabaseError(str(raw["error"]))
        rows = raw.get("result", raw)
        if not isinstance(rows, list):
            raise DatabaseError(str(rows))
        errors = [r for r in rows if r.get("status") == "ERR"]
        if errors:
            raise DatabaseError("; ".join(str(r.get("result")) for r in errors))
        return serial([r.get("result") for r in rows])

    async def rows(self, sql, params=None):
        result = await self.query(sql, params)
        return result[-1] if result else []

    async def get(self, id):
        rows = await self.rows("SELECT * FROM $id;", {"id": ensure_record_id(id)})
        return rows[0] if rows else None
