"""Download a private Pipedrive file inventory; optionally import into local CRM.

Reads PIPEDRIVE_API_TOKEN from .env. Only GET requests to Pipedrive/storage.
Downloads are verified before any database writes. Credentials never follow
redirects to storage. Source file IDs are durable idempotency keys.
"""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from dotenv import dotenv_values
from starlette.datastructures import Headers, UploadFile

from overdrive.config import Settings
from overdrive.database import Database
from overdrive.service import CRM, Actor
from overdrive.storage import LocalStorage

ORIGIN = "https://api.pipedrive.com"


async def download(client, token, row, destination, maximum, origin=ORIGIN):
    origin_host = urlsplit(origin).hostname or ""
    if urlsplit(origin).scheme != "https" or not origin_host.endswith(".pipedrive.com"):
        raise ValueError("PIPEDRIVE_ORIGIN must be an HTTPS Pipedrive subdomain")
    url = f"{origin}/api/v1/files/{int(row['id'])}/download"
    for _ in range(5):
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or not (host == origin_host or host.endswith(".amazonaws.com"))
        ):
            raise ValueError(f"Unexpected download destination for file {row['id']}")
        headers = {"x-api-token": token} if host == origin_host else {}
        async with client.stream("GET", url, headers=headers) as response:
            if response.status_code in {301, 302, 303, 307, 308}:
                url = urljoin(url, response.headers["location"])
                continue
            if response.status_code != 200:
                raise ValueError(f"Download status {response.status_code} for file {row['id']}")
            total = 0
            with destination.open("wb") as out:
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > maximum:
                        raise ValueError(f"File {row['id']} exceeds upload limit")
                    out.write(chunk)
            return
    raise ValueError("Too many download redirects")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = args.directory
    inventory = json.loads((root / "files-inventory.json").read_text())
    mapping = json.loads((root / "mapping.json").read_text())
    settings = Settings()
    import os

    import_settings = {**dotenv_values(".env"), **os.environ}
    token = import_settings.get("PIPEDRIVE_API_TOKEN")
    origin = import_settings.get("PIPEDRIVE_ORIGIN", ORIGIN)
    if not token:
        raise ValueError("PIPEDRIVE_API_TOKEN is missing")
    folder = root / "files"
    folder.mkdir(mode=0o700, exist_ok=True)
    prepared = []
    async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
        for did, rows in inventory.items():
            for row in rows:
                if not row.get("active_flag", True):
                    continue
                if row.get("deal_id") != int(did):
                    raise ValueError("File/deal association mismatch")
                name = Path(row["name"]).name
                path = folder / f"{int(row['id'])}_{name}"
                if not path.exists():
                    temp = path.with_suffix(path.suffix + ".part")
                    try:
                        await download(client, token, row, temp, settings.max_upload_bytes, origin)
                        temp.chmod(0o600)
                        temp.replace(path)
                    finally:
                        temp.unlink(missing_ok=True)
                data = path.read_bytes()
                if not data or len(data) != row["file_size"] or len(data) > settings.max_upload_bytes:
                    raise ValueError(f"Size mismatch for file {row['id']}")
                if name.lower().endswith(".pdf") and not data.startswith(b"%PDF-"):
                    raise ValueError(f"Invalid PDF for file {row['id']}")
                if name.lower().endswith(".md"):
                    data.decode("utf-8")
                prepared.append((did, row, path, hashlib.sha256(data).hexdigest()))
                print(f"Verified file {row['id']}: {len(data)} bytes", flush=True)
    report = {
        "downloaded": len(prepared),
        "bytes": sum(p.stat().st_size for _, _, p, _ in prepared),
        "applied": args.apply,
        "files": [],
    }
    if args.apply:
        db = Database(settings)
        await db.connect()
        try:
            crm = CRM(db)
            actor = Actor(mapping["actor"], "import", "Pipedrive files")
            if not (await crm.user(actor))["admin"]:
                raise ValueError("Import requires admin")
            storage = LocalStorage(settings.storage_path, settings.max_upload_bytes)
            for did, row, path, checksum in prepared:
                mime = "application/pdf" if path.suffix.lower() == ".pdf" else "text/markdown"
                desc = (row.get("description") or "") + (
                    f"\n\nPipedrive file #{row['id']} · Uploaded {row['add_time']} UTC"
                )
                with path.open("rb") as stream:
                    upload = UploadFile(stream, filename=row["name"], headers=Headers({"content-type": mime}))
                    saved = await storage.save(
                        crm,
                        actor,
                        f"deal:pipedrive_{did}",
                        upload,
                        key=f"pipedrive-file:{row['id']}:deal:{did}",
                        title=row["name"],
                        description=desc,
                    )
                actual = await crm.get(actor, saved["id"])
                # Explicit historical status only; don't infer from dates or deal outcome.
                outdated = "versão substituída" in (row.get("description") or "").lower()
                if outdated and actual["version"] == 1 and not actual["deprecated"]:
                    actual = await crm.save(
                        actor,
                        "attachment",
                        {"deprecated": True},
                        id=actual["id"],
                        version=actual["version"],
                        key=f"pipedrive-outdated:{row['id']}",
                    )
                content = storage.path(actual["storage_key"]).read_bytes()
                if hashlib.sha256(content).hexdigest() != checksum:
                    raise ValueError("Imported checksum mismatch")
                report["files"].append(
                    {
                        "source_id": row["id"],
                        "deal": did,
                        "target": actual["id"],
                        "name": row["name"],
                        "checksum": checksum,
                        "deprecated": actual["deprecated"],
                        "bytes": len(content),
                    }
                )
        finally:
            await db.close()
    output = root / ("files-import-report.json" if args.apply else "files-download-report.json")
    output.write_text(json.dumps(report, indent=2))
    output.chmod(0o600)
    print(json.dumps({k: v for k, v in report.items() if k != "files"}))


if __name__ == "__main__":
    asyncio.run(main())
