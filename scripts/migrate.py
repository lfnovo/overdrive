"""Usage: PYTHONPATH=. uv run python scripts/migrate.py status|up|baseline --expect-ns NS --expect-db DB"""

import argparse
import asyncio

from overdrive.config import Settings
from overdrive.database import Database
from overdrive.migrations import migrate


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["status", "up", "baseline"])
    parser.add_argument("--expect-ns", required=True)
    parser.add_argument("--expect-db", required=True)
    args = parser.parse_args()
    settings = Settings()
    if (args.expect_ns, args.expect_db) != (settings.surreal_namespace, settings.surreal_database):
        parser.error("Configured namespace/database does not match the expected target; nothing was executed")
    db = Database(settings)
    await db.connect()
    try:
        result = await migrate(db, args.action)
        if args.action == "status":
            print(f"Schema version: {result['current_version']}; pending: {len(result['pending'])}")
        else:
            print(f"{args.action}: {len(result)} migration(s)")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
