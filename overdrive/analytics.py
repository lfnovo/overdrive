"""Observed pipeline analytics; imported snapshots never imply missing stage visits."""

from collections import defaultdict
from datetime import UTC, datetime
from statistics import median

from .stages import LEGACY_STAGES, STAGES


def instant(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    except (ValueError, AttributeError):
        return None


def calculate(deals, events, now):
    by_deal = defaultdict(list)
    for event in events:
        by_deal[event["target"]].append(event)
    visits = {stage: set() for stage in STAGES}
    progressed = {stage: set() for stage in STAGES}
    durations = {stage: [] for stage in STAGES}
    current = {stage: [] for stage in STAGES}
    incomplete = 0
    ages = []
    cycles = []
    for deal in deals:
        history = sorted(
            by_deal[deal["id"]],
            key=lambda e: (e["created_at"], e.get("changes", {}).get("after", {}).get("version", 0)),
        )
        state = None
        started = None
        complete_start = False
        full_history = False
        created = None
        last_won = None
        reached = set()
        for event in history:
            stamp = instant(event["created_at"])
            changes = event.get("changes", {})
            before, after = dict(changes.get("before", {})), dict(changes.get("after", {}))
            for snapshot in (before, after):
                if snapshot.get("stage") in LEGACY_STAGES:
                    snapshot["stage"] = LEGACY_STAGES[snapshot["stage"]]
            if not stamp or stamp > now or after.get("stage") not in STAGES:
                continue
            if state is None:
                full_history = event.get("action") == "deal.create" and not before
                created = stamp if full_history else None
                state = before if before.get("stage") in STAGES else after
                if state.get("outcome") == "open" and not state.get("archived"):
                    started = stamp
                    complete_start = full_history
            # Count only stages actually seen, never infer skipped earlier stages.
            reached.add(state["stage"])
            if STAGES.index(after["stage"]) > STAGES.index(state["stage"]):
                for stage in reached:
                    if STAGES.index(stage) < STAGES.index(after["stage"]):
                        progressed[stage].add(deal["id"])
            reached.add(after["stage"])
            was_active = state.get("outcome") == "open" and not state.get("archived")
            is_active = after.get("outcome") == "open" and not after.get("archived")
            changed = state["stage"] != after["stage"]
            # A discontinuity means the event stream is incomplete; do not time across it.
            if before and (
                before.get("stage") != state.get("stage")
                or before.get("outcome") != state.get("outcome")
                or before.get("archived") != state.get("archived")
            ):
                started, complete_start, full_history = stamp, False, False
            if was_active and (changed or not is_active) and started is not None:
                if complete_start:
                    durations[state["stage"]].append(max(0, (stamp - started).total_seconds() / 86400))
                started = None
            if is_active and (changed or not was_active):
                started, complete_start = stamp, True
            if after.get("outcome") == "won" and state.get("outcome") != "won":
                last_won = stamp
                if "Contract" in reached:
                    progressed["Contract"].add(deal["id"])
            elif after.get("outcome") != "won":
                last_won = None
            state = after
        if not full_history:
            incomplete += 1
        for stage in reached | {deal["stage"]}:
            if stage in visits:
                visits[stage].add(deal["id"])
        if full_history and created and last_won and deal["outcome"] == "won":
            cycles.append((last_won - created).total_seconds() / 86400)
        if deal["outcome"] == "open" and not deal["archived"]:
            age = None
            if (
                state
                and state["stage"] == deal["stage"]
                and state.get("outcome") == "open"
                and not state.get("archived")
                and started is not None
            ):
                age = max(0, (now - started).total_seconds() / 86400)
            item = {
                "id": deal["id"],
                "title": deal["title"],
                "stage": deal["stage"],
                "days": age,
                "partial": not complete_start,
                "value_cents": deal["value_cents"],
            }
            ages.append(item)
            current[deal["stage"]].append(item)
    won = sum(d["outcome"] == "won" for d in deals)
    lost = sum(d["outcome"] == "lost" for d in deals)
    stage_rows = []
    for stage in STAGES:
        count = len(visits[stage])
        stage_rows.append(
            {
                "name": stage,
                "reached": count,
                "progressed": len(progressed[stage]),
                "conversion": round(100 * len(progressed[stage]) / count, 1) if count else None,
                "median_days": round(median(durations[stage]), 2) if durations[stage] else None,
                "completed_visits": len(durations[stage]),
                "active": len(current[stage]),
                "value_cents": sum(d["value_cents"] for d in current[stage]),
            }
        )
    ages.sort(key=lambda x: (x["days"] is not None, x["days"] or 0), reverse=True)
    source_rows = []
    grouped = defaultdict(list)
    for deal in deals:
        grouped[deal.get("source")].append(deal)
    for source, rows in grouped.items():
        wins = sum(d["outcome"] == "won" for d in rows)
        losses = sum(d["outcome"] == "lost" for d in rows)
        source_rows.append(
            {
                "id": source,
                "deals": len(rows),
                "won": wins,
                "lost": losses,
                "win_rate": round(100 * wins / (wins + losses), 1) if wins + losses else None,
                "won_value_cents": sum(d["value_cents"] for d in rows if d["outcome"] == "won"),
            }
        )
    return {
        "as_of": now.isoformat(),
        "total": len(deals),
        "open": sum(d["outcome"] == "open" for d in deals),
        "won": won,
        "lost": lost,
        "win_rate": round(100 * won / (won + lost), 1) if won + lost else None,
        "open_value_cents": sum(
            d["value_cents"] for d in deals if d["outcome"] == "open" and not d["archived"]
        ),
        "won_value_cents": sum(d["value_cents"] for d in deals if d["outcome"] == "won"),
        "median_cycle_days": round(median(cycles), 2) if cycles else None,
        "cycle_sample": len(cycles),
        "incomplete_history": incomplete,
        "stages": stage_rows,
        "aging": ages[:15],
        "sources": sorted(source_rows, key=lambda x: x["deals"], reverse=True),
    }
