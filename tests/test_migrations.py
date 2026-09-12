import shutil

import pytest
from surreal_basics import SurrealDBMigrationError
from surreal_basics.migrate import AsyncMigrationRunner

from overdrive import migrations
from overdrive.database import Database, DatabaseError
from overdrive.service import Actor


async def test_schema_created_and_repeat_is_noop(system):
    app, _ = system
    db = app.state.db
    status = await migrations.migrate(db, "status")
    assert status["current_version"] == 2
    assert status["pending"] == []
    assert await migrations.migrate(db) == []
    await migrations.verify_initial_schema(db)


async def test_legacy_baseline_preserves_records_and_refuses_schema_drift(system):
    app, _ = system
    db, crm = app.state.db, app.state.crm
    actor = Actor("user:initial_admin")
    deal = await crm.save(actor, "deal", {"title": "Keep this", "value_cents": 98765})
    history = await crm.history(actor, deal["id"])
    await db.query("REMOVE TABLE _sbl_migrations;")
    with pytest.raises(RuntimeError, match="baseline"):
        await migrations.migrate(db)
    applied = await migrations.migrate(db, "baseline")
    assert [m.version for m in applied] == [1]
    assert await crm.get(actor, deal["id"]) == deal
    assert await crm.history(actor, deal["id"]) == history
    assert await migrations.migrate(db, "baseline") == []
    assert [m.version for m in await migrations.migrate(db)] == [2]
    await db.query("REMOVE FIELD expected_close ON deal;")
    with pytest.raises(RuntimeError, match="Schema differs"):
        await migrations.migrate(db, "baseline")


@pytest.mark.parametrize(
    "failing_sql",
    [
        "BEGIN; DEFINE TABLE migration_test SCHEMAFULL; THROW 'stop'; COMMIT;",
        "RETURN 1; THROW 'stop';",
    ],
)
async def test_failed_migration_is_not_recorded_and_can_be_retried(
    system, tmp_path, monkeypatch, failing_sql
):
    app, _ = system
    db = app.state.db
    shutil.copy(migrations.MIGRATIONS / "001_initial_schema.surrealql", tmp_path)
    shutil.copy(migrations.MIGRATIONS / "002_local_auth.surrealql", tmp_path)
    change = tmp_path / "003_example.surrealql"
    change.write_text(failing_sql)
    monkeypatch.setattr(migrations, "MIGRATIONS", tmp_path)
    with pytest.raises(SurrealDBMigrationError, match="stop"):
        await migrations.migrate(db)
    assert "migration_test" not in (await db.rows("INFO FOR DB;"))["tables"]
    assert (await AsyncMigrationRunner(tmp_path).status())["current_version"] == 2
    change.write_text("DEFINE TABLE IF NOT EXISTS migration_test SCHEMAFULL;")
    assert [m.version for m in await migrations.migrate(db)] == [3]
    assert await migrations.migrate(db) == []


async def test_adapter_retains_all_results_and_rejects_second_owner(system):
    app, _ = system
    db = app.state.db
    assert await db.query("RETURN 1; RETURN 2;") == [1, 2]
    with pytest.raises(DatabaseError, match="adapter-error"):
        await db.query("THROW 'adapter-error'; RETURN 2;")
    second = Database(app.state.settings)
    with pytest.raises(RuntimeError, match="already active"):
        await second.connect()
    await second.close()
    assert await db.rows("RETURN 3;") == 3
