import csv
from datetime import datetime
from decimal import Decimal
from pathlib import Path


def test_exported_demo_csv_baselines():
    directory = Path(__file__).resolve().parents[2] / "samples/demos/data"
    tables = {}
    for path in directory.glob("*.csv"):
        with path.open(encoding="utf-8", newline="") as stream:
            tables[path.stem] = list(csv.DictReader(stream))
    assert {name: len(rows) for name, rows in tables.items()} == {
        "behavior.customers": 1000,
        "behavior.visit_events": 2000,
        "behavior.cart_events": 880,
        "behavior.order_events": 340,
        "market.channels": 2,
        "market.ad_spend": 8,
        "market.orders": 360,
        "market.sales_summary": 4,
    }
    visits = tables["behavior.visit_events"]
    assert {row["device"] for row in visits} == {"windows", "macos"}
    for start, stop, expected in (
        ("2026-09-01", "2026-09-08", (1000, 500, 200)),
        ("2026-09-08", "2026-09-15", (1000, 380, 140)),
    ):
        lo, hi = (datetime.fromisoformat(day + "T00:00:00+08:00") for day in (start, stop))
        current = {
            name: [row for row in tables["behavior." + name] if lo <= datetime.fromisoformat(row["occurred_at"]) < hi]
            for name in ("visit_events", "cart_events", "order_events")
        }
        first = {}
        for row in sorted(current["visit_events"], key=lambda r: (r["occurred_at"], r["event_id"])):
            first.setdefault(row["user_id"], datetime.fromisoformat(row["occurred_at"]))
        carted, ordered = set(), set()
        for cart in current["cart_events"]:
            user = cart["user_id"]
            when = datetime.fromisoformat(cart["occurred_at"])
            if user not in first or when <= first[user]:
                continue
            carted.add(user)
            if any(
                order["user_id"] == user
                and order["status"] in {"paid", "completed"}
                and datetime.fromisoformat(order["occurred_at"]) > when
                for order in current["order_events"]
            ):
                ordered.add(user)
        assert (len(first), len(carted), len(ordered)) == expected
        orders = {
            row["order_id"]: row
            for row in tables["market.orders"]
            if row["status"] in {"paid", "completed"}
            and row["paid_at"] != "\\N"
            and lo <= datetime.fromisoformat(row["paid_at"]) < hi
        }
        revenue = sum(Decimal(row["paid_amount"]) for row in orders.values())
        spend = sum(
            Decimal(row["amount"]) for row in tables["market.ad_spend"] if start <= row["attributed_date"] < stop
        )
        assert (revenue, len(orders), spend) == (
            (Decimal("20000"), 200, Decimal("2000"))
            if start == "2026-09-01"
            else (Decimal("16000"), 160, Decimal("2500"))
        )
