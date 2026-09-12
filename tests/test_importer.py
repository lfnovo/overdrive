import pytest

from overdrive.importer import import_export
from overdrive.service import Actor


async def test_import_dry_run_and_retry(system, tmp_path):
    app, _ = system
    crm = app.state.crm
    s = app.state.settings
    file = tmp_path / "deals.csv"
    file.write_text(
        "source_id,title,unit,value_brl,stage,file_paths,link_url\n1,Imported,Consultoria,48000,Proposta,proposal.md;proposal.pdf,https://example.test/deck\n"
    )
    (tmp_path / "proposal.md").write_text("# Imported proposal")
    (tmp_path / "proposal.pdf").write_bytes(b"%PDF-import")
    preview = await import_export(crm, s, file)
    assert preview["rows"] == 1 and preview["value_cents"] == 4800000
    assert await crm.listing(Actor("user:initial_admin"), "deal") == []
    first = await import_export(crm, s, file, True)
    second = await import_export(crm, s, file, True)
    assert not first["errors"] and not second["errors"], second
    assert first["deals"] == second["deals"]
    assert len(await crm.listing(Actor("user:initial_admin"), "deal")) == 1
    assert len(await crm.listing(Actor("user:initial_admin"), "attachment")) == 3
    file.write_text("source_id,title,value_brl\n2,Bad,10.123\n")
    with pytest.raises(ValueError):
        await import_export(crm, s, file, True)
