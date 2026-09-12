"""Bootstrap only the configured Overdrive namespace/database; root credentials from environment."""

import asyncio
import json
import os

from surrealdb import AsyncSurreal

from overdrive.config import Settings
from overdrive.database import Database
from overdrive.migrations import migrate


async def main():
    s = Settings()
    async with AsyncSurreal(s.surreal_url) as db:
        await db.signin({"username": os.environ["BOOTSTRAP_USER"], "password": os.environ["BOOTSTRAP_PASS"]})
        for value in [s.surreal_namespace, s.surreal_database, s.surreal_user]:
            assert value.replace("_", "").isalnum()
        await db.query(
            f"DEFINE NAMESPACE IF NOT EXISTS {s.surreal_namespace}; USE NS {s.surreal_namespace}; "
            f"DEFINE DATABASE IF NOT EXISTS {s.surreal_database};"
        )
        await db.use(s.surreal_namespace, s.surreal_database)
        await db.query(
            f"DEFINE USER IF NOT EXISTS {s.surreal_user} ON DATABASE PASSWORD {json.dumps(s.surreal_pass)} ROLES EDITOR;"
        )
    app_db = Database(s)
    await app_db.connect()
    try:
        await migrate(app_db)
    finally:
        await app_db.close()
    print("Migrations applied; database-scoped application user ready.")


asyncio.run(main())
