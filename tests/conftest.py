import os
import uuid

import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from surrealdb import AsyncSurreal

from overdrive.app import create_app
from overdrive.config import Settings
from overdrive.database import Database
from overdrive.migrations import migrate


@pytest_asyncio.fixture
async def system(tmp_path):
    import json

    namespace = "overdrive_test_" + uuid.uuid4().hex[:10]
    s = Settings(
        surreal_namespace=namespace,
        surreal_database="test",
        app_url="http://localhost:18765",
        dev_login=True,
        dev_login_email="",
        admin_email="admin@example.test",
        storage_path=tmp_path / "files",
    )
    async with AsyncSurreal(s.surreal_url) as root:
        await root.signin(
            {"username": os.environ["BOOTSTRAP_USER"], "password": os.environ["BOOTSTRAP_PASS"]}
        )
        await root.query(f"DEFINE NAMESPACE {namespace}; USE NS {namespace}; DEFINE DATABASE test;")
        await root.use(namespace, "test")
        await root.query(
            f"DEFINE USER {s.surreal_user} ON DATABASE PASSWORD {json.dumps(s.surreal_pass)} ROLES EDITOR;"
        )
        schema_db = Database(s)
        await schema_db.connect()
        try:
            await migrate(schema_db)
        finally:
            await schema_db.close()
        app = create_app(s)
        try:
            async with (
                LifespanManager(app),
                AsyncClient(
                    transport=ASGITransport(app=app, client=("127.0.0.1", 1234)),
                    base_url=s.app_url,
                    follow_redirects=False,
                ) as client,
            ):
                yield app, client
        finally:
            await root.query(f"REMOVE NAMESPACE {namespace};")
