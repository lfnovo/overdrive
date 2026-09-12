"""Run with the application stopped. Uses the configured local database."""

import asyncio
import json

from overdrive.config import Settings
from overdrive.database import Database
from overdrive.service import CRM
from overdrive.stage_migration import migrate_stages


async def main():
    db = Database(Settings())
    await db.connect()
    try:
        print(json.dumps(await migrate_stages(CRM(db))))
    finally:
        await db.close()


asyncio.run(main())
