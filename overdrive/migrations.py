"""Versioned schema changes using surreal-basics; run with one migration process."""

import re
from pathlib import Path

from surreal_basics.migrate import AsyncMigrationRunner

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


def canonical(ddl):
    ddl = ddl.replace("IF NOT EXISTS ", "").replace("TYPE NORMAL ", "")
    ddl = ddl.replace(" PERMISSIONS FULL", "").replace('"', "'")
    ddl = re.sub(r"option<([^<>]*(?:<[^<>]*>)?)>", r"none | \1", ddl)
    return " ".join(ddl.rstrip(";").split())


async def verify_initial_schema(db):
    """Check existing table, field and index definitions before recording baseline 1."""
    db.check_target()
    info = await db.rows("INFO FOR DB;")
    tables = info["tables"]
    definitions = re.findall(
        r"DEFINE (TABLE|FIELD|INDEX) IF NOT EXISTS (\w+)(?: ON (\w+))? ([^;]+);",
        (MIGRATIONS / "001_initial_schema.surrealql").read_text(),
    )
    table_info = {}
    mismatches = []
    for kind, name, table, rest in definitions:
        table = table or name
        if table not in tables:
            mismatches.append(f"Missing table: {table}")
            continue
        if kind == "TABLE":
            actual = tables[table]
            expected = f"DEFINE TABLE {name} {rest}"
        else:
            if table not in table_info:
                table_info[table] = await db.rows(f"INFO FOR TABLE {table};")
            actual = table_info[table]["fields" if kind == "FIELD" else "indexes"].get(name, "")
            expected = f"DEFINE {kind} {name} ON {table} {rest}"
        if canonical(actual) != canonical(expected):
            mismatches.append(f"Schema differs: {kind.lower()} {table}.{name}")
    if not definitions:
        raise RuntimeError("Initial migration contains no schema definitions")
    if mismatches:
        raise RuntimeError("Cannot baseline this database. " + "; ".join(sorted(set(mismatches))))


async def migrate(db, action="up"):
    db.check_target()
    runner = AsyncMigrationRunner(MIGRATIONS)
    if action == "baseline":
        await verify_initial_schema(db)
        return await runner.baseline(target_version=1)
    if action == "status":
        return await runner.status()
    if action != "up":
        raise ValueError("Unknown migration action")
    tables = (await db.rows("INFO FOR DB;"))["tables"]
    applied = await runner.get_applied_versions() if "_sbl_migrations" in tables else []
    if not applied and any(not table.startswith("_sbl_") for table in tables):
        raise RuntimeError("Existing schema without migration tracking. Verify and baseline it first.")
    return await runner.run_up()
