"""Import an offline MCP snapshot using an explicit private mapping; dry run by default."""

import argparse
import asyncio
import json
from pathlib import Path

from overdrive.config import Settings
from overdrive.database import Database
from overdrive.pipedrive_import import apply_plan, build_plan
from overdrive.service import CRM


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("mapping", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    snapshot = json.loads(args.snapshot.read_text())
    mapping = json.loads(args.mapping.read_text())
    records = build_plan(snapshot, mapping)
    db = Database(Settings())
    await db.connect()
    try:
        report = await apply_plan(CRM(db), mapping["actor"], records, apply=args.apply)
        output = args.snapshot.parent / ("import-report.json" if args.apply else "dry-run.json")
        output.write_text(json.dumps(report, indent=2))
        output.chmod(0o600)
        print(json.dumps({k: v for k, v in report.items() if k != "ids"}, indent=2))
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
