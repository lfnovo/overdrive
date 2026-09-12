"""Map only known old stages; preserve history and leave the six current stages untouched."""

from .service import Actor
from .stages import LEGACY_STAGES, STAGES


async def migrate_stages(crm):
    admins = await crm.db.rows("SELECT * FROM user WHERE admin = true AND active = true LIMIT 1;")
    if not admins:
        raise ValueError("An active administrator is required.")
    actor = Actor(admins[0]["id"], "migration", "six-stage-pipeline")
    rows = await crm.db.rows("SELECT * FROM deal;")
    unknown = [r["id"] for r in rows if r["stage"] not in {*STAGES, *LEGACY_STAGES}]
    if unknown:
        raise ValueError("Unknown stages; review before migrating: " + ", ".join(unknown))
    count = 0
    for row in rows:
        if row["stage"] in LEGACY_STAGES:
            await crm.save(
                actor, "deal", {"stage": LEGACY_STAGES[row["stage"]]}, id=row["id"], version=row["version"]
            )
            count += 1
    return {"deals_updated": count}
