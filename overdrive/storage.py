import hashlib
import os
import uuid
from pathlib import Path

from pydantic import ValidationError

from .models import Attachment
from .service import DomainError, new_id


class LocalStorage:
    def __init__(self, root: Path, max_size: int):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_size = max_size

    def path(self, key):
        if len(key) != 32 or any(c not in "0123456789abcdef" for c in key):
            raise DomainError("File not found.", 404, "not_found")
        return self.root / key

    async def save(self, crm, actor, deal, upload, key="", *, title="", description=""):
        await crm.get(actor, deal, "deal")
        user = await crm.user(actor)
        try:
            metadata = Attachment(
                deal=deal,
                title=title or Path(upload.filename or "file").name[:200],
                description=description,
            ).model_dump()
        except ValidationError as exc:
            raise DomainError("Invalid title or description.") from exc
        storage_key = uuid.uuid4().hex
        temp = self.root / (storage_key + ".tmp")
        digest, size = hashlib.sha256(), 0
        try:
            with temp.open("xb") as out:
                while chunk := await upload.read(65536):
                    size += len(chunk)
                    if size > self.max_size:
                        raise DomainError("The file exceeds the 25 MB limit.", 413, "too_large")
                    digest.update(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            if not size:
                raise DomainError("The file is empty.")
            record = {
                **metadata,
                "created_by": actor.id,
                "creator_name": user["name"],
                "name": Path(upload.filename or "file").name[:200],
                "mime": upload.content_type or "application/octet-stream",
                "size": size,
                "checksum": digest.hexdigest(),
                "storage_key": storage_key,
            }
            async with crm.db.lock:
                await crm.get(actor, deal, "deal")
                payload = {k: v for k, v in record.items() if k not in {"storage_key", "creator_name"}}
                replay = await crm._replay(actor, "attachment.create", key, payload)
                if replay:
                    return replay
                os.replace(temp, self.path(storage_key))
                try:
                    return await crm._commit(
                        actor,
                        [(new_id("attachment"), None, record)],
                        "attachment.create",
                        audit_deal=deal,
                        key=key,
                        payload=payload,
                    )
                except Exception:
                    self.path(storage_key).unlink(missing_ok=True)
                    raise
        finally:
            temp.unlink(missing_ok=True)

    async def delete(self, crm, actor, id, version, key):
        if not key:
            raise DomainError("Provide an idempotency key to delete.")
        payload = {"id": id, "version": version}
        async with crm.db.lock:
            await crm.user(actor)
            receipt = await crm._replay(actor, "attachment.delete", key, payload)
            if not receipt:
                record = await crm.get(actor, id, "attachment")
                if record["version"] != version:
                    raise DomainError("This item changed. Reload before deleting.", 409, "conflict")
                receipt = await crm._commit(
                    actor,
                    [(id, record, None)],
                    "attachment.delete",
                    audit_deal=record["deal"],
                    key=key,
                    payload=payload,
                )
            if receipt.get("storage_key"):
                try:
                    self.path(receipt["storage_key"]).unlink(missing_ok=True)
                except OSError as error:
                    raise DomainError(
                        "The item was removed from the deal, but file cleanup failed. Retry the same request.",
                        503,
                        "file_cleanup_failed",
                    ) from error
            return {k: v for k, v in receipt.items() if k != "storage_key"}
