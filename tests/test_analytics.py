import re
from datetime import UTC, datetime

import pytest

from overdrive.analytics import calculate
from overdrive.service import Actor, DomainError


def state(stage="New Lead", outcome="open", archived=False):
    return {
        "id": "deal:a",
        "title": "Journey",
        "stage": stage,
        "outcome": outcome,
        "archived": archived,
        "value_cents": 10000,
        "created_at": "2026-01-01T00:00:00+00:00",
    }


def event(day, before, after, action="deal.update"):
    return {
        "target": "deal:a",
        "action": action,
        "created_at": f"2026-01-{day:02d}T00:00:00+00:00",
        "changes": {"before": before, "after": after},
    }


def test_progress_skips_returns_pauses_and_win():
    a = state()
    b = state("Proposal Sent")
    c = state("Briefing Ready")
    paused = state("Briefing Ready", archived=True)
    contract = state("Contract")
    won = state("Contract", "won")
    history = [
        event(1, {}, a, "deal.create"),
        event(3, a, b),
        event(6, b, c),
        event(8, c, paused),
        event(10, paused, c),
        event(11, c, contract),
        event(13, contract, won),
    ]
    report = calculate([won], history, datetime(2026, 1, 20, tzinfo=UTC))
    rows = {s["name"]: s for s in report["stages"]}
    assert rows["New Lead"]["median_days"] == 2
    assert rows["Proposal Sent"]["median_days"] == 3
    assert rows["Briefing Ready"]["median_days"] == 1.5  # 2d and 1d stays, archive excluded.
    assert rows["Briefing Ready"]["completed_visits"] == 2
    assert rows["Proposal Ready"]["reached"] == 0
    assert rows["Proposal Ready"]["conversion"] is None
    assert rows["New Lead"]["conversion"] == 100
    assert rows["Proposal Sent"]["progressed"] == 1  # Later reached Contract after returning.
    assert rows["Contract"]["conversion"] == 100
    assert report["median_cycle_days"] == 12
    assert report["win_rate"] == 100
    assert report["aging"] == []


def test_imported_first_stay_unknown_and_current_lower_bound():
    a = state("Proposal Sent")
    history = [event(5, {}, a, "pipedrive.import")]
    report = calculate([a], history, datetime(2026, 1, 10, tzinfo=UTC))
    assert report["incomplete_history"] == 1
    assert report["aging"][0]["partial"]
    assert report["aging"][0]["days"] == 5
    assert report["stages"][0]["reached"] == 0
    b = state("Negotiation")
    report = calculate([b], history + [event(8, a, b)], datetime(2026, 1, 10, tzinfo=UTC))
    assert report["stages"][3]["median_days"] is None
    assert report["stages"][3]["conversion"] == 100
    assert report["aging"][0]["days"] == 2
    assert not report["aging"][0]["partial"]
    assert report["win_rate"] is None
    assert calculate([b], [], datetime(2026, 1, 10, tzinfo=UTC))["aging"][0]["days"] is None


async def test_analytics_permissions_cohort_sources_and_rendering(system):
    app, client = system
    crm = app.state.crm
    admin = Actor("user:initial_admin")
    unit = await crm.save(admin, "unit", {"name": "Visible unit"})
    source = await crm.save(admin, "source", {"name": "Event"})
    user = await crm.save(
        admin, "user", {"name": "Member", "email": "member@example.test", "units": [unit["id"]]}
    )
    member = Actor(user["id"])
    visible = await crm.save(admin, "deal", {"title": "Visible", "unit": unit["id"], "source": source["id"]})
    await crm.save(admin, "deal", {"title": "Hidden", "value_cents": 999999})
    visible = await crm.save(
        admin,
        "deal",
        {"stage": "Proposal Sent", "outcome": "won", "archived": True},
        id=visible["id"],
        version=visible["version"],
    )
    report = await crm.analytics(member)
    assert report["total"] == 1 and report["won"] == 1
    assert report["stages"][0]["conversion"] == 100
    assert report["stages"][0]["completed_visits"] == 1
    assert report["incomplete_history"] == 0
    assert report["sources"][0]["id"] == source["id"]
    assert (await crm.analytics(member, include_archived=False))["total"] == 0
    assert (await crm.analytics(member, created_to="2000-01-01"))["total"] == 0
    assert (await crm.analytics(member, source="none"))["total"] == 0
    assert (await crm.analytics(member, unit="none"))["total"] == 0
    with pytest.raises(DomainError):
        await crm.analytics(member, created_from="2026-02-01", created_to="2026-01-01")
    app.state.settings.dev_login_email = user["email"]
    login = await client.get("/login")
    csrf = re.search(r'name="csrf" value="([^"]+)"', login.text)[1]
    await client.post("/auth/dev", data={"csrf": csrf})
    page = await client.get("/analytics")
    assert page.status_code == 200, page.text
    assert "Find your momentum" in page.text and "Hidden" not in page.text
    assert (await client.get("/api/analytics")).json()["total"] == 1
    empty = await client.get("/analytics?created_to=2000-01-01")
    assert empty.status_code == 200 and "No deals in this view yet." in empty.text
    assert (await client.get("/analytics?created_from=invalid")).status_code == 400
