"""Usage: PYTHONPATH=. uv run python scripts/backup.py backup|restore PATH"""

import argparse
import asyncio
import json
from pathlib import Path

from overdrive.backup import export_backup, restore_backup
from overdrive.config import Settings
from overdrive.database import Database


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["backup", "restore"])
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    settings = Settings()
    db = Database(settings)
    await db.connect()
    try:
        operation = export_backup if args.action == "backup" else restore_backup
        print(json.dumps(await operation(db, settings, args.path), indent=2))
    finally:
        await db.close()


asyncio.run(main())
