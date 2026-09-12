"""Run after backup and bootstrap, with the local application stopped."""

import asyncio
import json

from overdrive.config import Settings
from overdrive.database import Database
from overdrive.material_migration import migrate_materials
from overdrive.service import CRM


async def main():
    settings = Settings()
    db = Database(settings)
    await db.connect()
    try:
        print(json.dumps(await migrate_materials(CRM(db), settings), indent=2))
    finally:
        await db.close()


asyncio.run(main())
