import pytest

from overdrive.pipedrive_import import apply_plan, complete_data, money, plain_html, timestamp
from overdrive.service import CRM, Actor, DomainError


def test_source_conversion_is_lossless_and_rejects_incomplete_pages():
    assert money(84375, "BRL") == 8437500
    assert money("19.99", "BRL") == 1999
    with pytest.raises(ValueError):
        money("1.001", "BRL")
    with pytest.raises(ValueError):
        money(10, "USD")
    with pytest.raises(ValueError):
        complete_data({"success": True, "data": [], "additional_data": {"next_cursor": "next"}})
    with pytest.raises(ValueError):
        complete_data(
            {"success": True, "additional_data": {"pagination": {"more_items_in_collection": True}}}
        )
    assert timestamp("2026-08-23 20:29:44") == "2026-08-23T20:29:44+00:00"
    text = plain_html('<p>A &amp; B</p><p><a href="https://example.com">Source</a></p><script>bad()</script>')
    assert "A & B" in text and "https://example.com" in text and "bad()" not in text


async def test_import_retry_preserves_edits_dates_and_authorization(system):
    app, _ = system
    crm = CRM(app.state.db)
    actor = Actor("user:initial_admin")
    unit = await crm.save(actor, "unit", {"name": "Test import"})
    records = [
        (
            "deal:pipedrive_900",
            {
                "title": "Source deal",
                "unit": unit["id"],
                "owner": actor.id,
                "stage": "Proposal Sent",
                "outcome": "won",
                "value_cents": 1999,
                "loss_reason": "",
                "archived": False,
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        )
    ]
    dry = await apply_plan(crm, actor.id, records)
    assert dry["new"] == {"deal": 1}
    assert await crm.db.get(records[0][0]) is None
    await apply_plan(crm, actor.id, records, apply=True)
    deal = await crm.get(actor, records[0][0])
    assert deal["created_at"] == records[0][1]["created_at"]
    await crm.save(actor, "deal", {"title": "Human edit"}, id=deal["id"], version=deal["version"])
    repeat = await apply_plan(crm, actor.id, records, apply=True)
    assert repeat["new"] == {} and repeat["skipped_existing"] == 1
    assert (await crm.get(actor, deal["id"]))["title"] == "Human edit"
    member = await crm.save(actor, "user", {"name": "Member", "email": "member@example.com", "units": []})
    with pytest.raises(DomainError):
        await apply_plan(crm, member["id"], records, apply=True)
    broken = [("deal:pipedrive_901", {**records[0][1], "owner": member["id"]})]
    with pytest.raises(ValueError, match="Owner cannot access"):
        await apply_plan(crm, actor.id, broken, apply=True)
    assert await crm.db.get("deal:pipedrive_901") is None
