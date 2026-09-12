import argparse
import asyncio
import json
from pathlib import Path

from overdrive.config import Settings
from overdrive.database import Database
from overdrive.importer import import_export
from overdrive.service import CRM


async def main():
    p = argparse.ArgumentParser(description="Normalized CSV: validate by default, write only with --apply.")
    p.add_argument("path", type=Path)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--report", type=Path, default=Path(".local/import-report.json"))
    args = p.parse_args()
    s = Settings()
    db = Database(s)
    await db.connect()
    try:
        report = await import_export(CRM(db), s, args.path, args.apply)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        print(json.dumps(report, indent=2, ensure_ascii=False))
        if report["errors"]:
            raise SystemExit(1)
    finally:
        await db.close()


asyncio.run(main())
