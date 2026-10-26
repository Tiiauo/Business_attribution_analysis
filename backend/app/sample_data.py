"""Deterministic, additive sample provisioning; never truncate existing tables."""

import argparse
import asyncio
import json
import re
from datetime import datetime, timedelta
from decimal import Decimal
from random import Random

import asyncpg
from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.core.audit import record_event
from app.core.config import get_settings
from app.core.database import Session
from app.core.security import random_token
from app.models import DataSource
from app.runtime import (
    initialize_runtime,
    merge_config,
    resolve_credential,
    runtime_row,
    save_draft,
    store_credential,
    validate_bundle,
)

SEED = 20260901
PERIODS = [datetime.fromisoformat("2026-09-01T10:00:00+08:00"), datetime.fromisoformat("2026-09-08T10:00:00+08:00")]

# Column descriptions and relationships are also exported in docs/M02数据字典.md.
TABLES = {
    "catalog": {
        "categories": "category_id text PRIMARY KEY, name text NOT NULL",
        "products": "product_id text PRIMARY KEY, category_id text REFERENCES catalog.categories, name text NOT NULL, price numeric(12,2) NOT NULL",
        "search_impressions": "event_id text PRIMARY KEY, product_id text REFERENCES catalog.products, user_id text NOT NULL, keyword text, occurred_at timestamptz NOT NULL",
        "clicks": "event_id text PRIMARY KEY, impression_id text REFERENCES catalog.search_impressions, occurred_at timestamptz NOT NULL",
        "conversions": "conversion_id text PRIMARY KEY, click_id text REFERENCES catalog.clicks, order_id text NOT NULL, amount numeric(12,2), occurred_at timestamptz NOT NULL",
    },
    "behavior": {
        "customers": "user_id text PRIMARY KEY, registered_at timestamptz NOT NULL, segment text",
        "visit_events": "event_id text PRIMARY KEY, user_id text REFERENCES behavior.customers, occurred_at timestamptz NOT NULL, device text NOT NULL, channel text",
        "cart_events": "event_id text PRIMARY KEY, user_id text REFERENCES behavior.customers, occurred_at timestamptz NOT NULL, device text, product_id text NOT NULL",
        "order_events": "event_id text PRIMARY KEY, user_id text REFERENCES behavior.customers, occurred_at timestamptz NOT NULL, device text, order_id text UNIQUE, amount numeric(12,2), status text NOT NULL",
    },
    "inventory": {
        "stocks": "snapshot_id text PRIMARY KEY, product_id text NOT NULL, warehouse_id text NOT NULL, snapshot_at timestamptz NOT NULL, quantity integer NOT NULL",
        "inbound": "document_id text PRIMARY KEY, product_id text NOT NULL, warehouse_id text NOT NULL, occurred_at timestamptz NOT NULL, quantity integer NOT NULL",
        "outbound": "document_id text PRIMARY KEY, product_id text NOT NULL, warehouse_id text NOT NULL, occurred_at timestamptz NOT NULL, quantity integer NOT NULL",
        "sales": "sale_id text PRIMARY KEY, outbound_id text REFERENCES inventory.outbound, product_id text NOT NULL, warehouse_id text NOT NULL, occurred_at timestamptz NOT NULL, quantity integer NOT NULL",
    },
    "feedback": {
        "products": "product_id text PRIMARY KEY, name text NOT NULL",
        "reviews": "review_id text PRIMARY KEY, product_id text REFERENCES feedback.products, user_id text NOT NULL, order_id text NOT NULL, occurred_at timestamptz NOT NULL, content text",
        "ratings": "rating_id text PRIMARY KEY, review_id text REFERENCES feedback.reviews, score integer CHECK(score BETWEEN 1 AND 5), occurred_at timestamptz NOT NULL",
        "refunds": "refund_id text PRIMARY KEY, review_id text REFERENCES feedback.reviews, order_id text NOT NULL, amount numeric(12,2), reason text, completed_at timestamptz NOT NULL",
    },
    "market": {
        "channels": "channel_id text PRIMARY KEY, name text NOT NULL",
        "ad_spend": "spend_id text PRIMARY KEY, channel_id text REFERENCES market.channels, campaign_id text NOT NULL, attributed_date date NOT NULL, amount numeric(12,2) NOT NULL",
        "orders": "order_id text PRIMARY KEY, user_id text NOT NULL, created_at timestamptz NOT NULL, paid_at timestamptz, status text NOT NULL, channel_id text REFERENCES market.channels, paid_amount numeric(12,2) NOT NULL, currency text NOT NULL",
        "sales_summary": "summary_id text PRIMARY KEY, channel_id text REFERENCES market.channels, period_start date NOT NULL, currency text NOT NULL, order_count integer NOT NULL, revenue numeric(12,2) NOT NULL",
    },
    "refund": {
        "customers": "user_id text PRIMARY KEY, segment text, registered_at timestamptz NOT NULL",
        "orders": "order_id text PRIMARY KEY, user_id text REFERENCES refund.customers, paid_at timestamptz NOT NULL, paid_amount numeric(12,2) NOT NULL, currency text NOT NULL",
        "reasons": "reason_code text PRIMARY KEY, description text NOT NULL",
        "requests": "request_id text PRIMARY KEY, order_id text REFERENCES refund.orders, reason_code text REFERENCES refund.reasons, requested_at timestamptz NOT NULL, completed_at timestamptz, status text NOT NULL, amount numeric(12,2) NOT NULL",
    },
}
NAMES = dict(
    zip(
        TABLES,
        ("商品目录优化", "客户行为分析", "库存异常分析", "评论反馈分析", "市场表现分析", "退款模式分析"),
        strict=True,
    )
)


def rows():
    data = {f"{schema}.{table}": [] for schema, tables in TABLES.items() for table in tables}
    random = Random(SEED)
    data["catalog.categories"] = [("c1", "日用")]
    data["catalog.products"] = [("p1", "c1", "合成商品", Decimal("100.00"))]
    data["feedback.products"] = [("p1", "合成商品")]
    data["market.channels"] = [("A", "渠道 A"), ("B", "渠道 B")]
    data["refund.customers"] = [("u1", "新客", PERIODS[0] - timedelta(days=30))]
    data["refund.reasons"] = [("quality", "质量问题")]
    data["behavior.customers"] = [(f"u{i:04d}", PERIODS[0] - timedelta(days=30), "合成客户") for i in range(1000)]
    for period, timestamp in enumerate(PERIODS):
        for i in range(10):
            key = f"{period}-{i}"
            data["catalog.search_impressions"].append(
                ("i" + key, "p1", f"u{i}", "合成", timestamp + timedelta(minutes=i))
            )
            if i < 5 - period:
                data["catalog.clicks"].append(("c" + key, "i" + key, timestamp + timedelta(minutes=i, seconds=1)))
            if i < 2 - period:
                data["catalog.conversions"].append(
                    ("v" + key, "c" + key, "o" + key, Decimal("100.00"), timestamp + timedelta(minutes=i, seconds=2))
                )
        for i in range(1000):
            windows = i < 600
            device = "windows" if windows else "macos"
            index = i if windows else i - 600
            key = f"{period}-{i:04d}"
            user = f"u{i:04d}"
            data["behavior.visit_events"].append(("v" + key, user, timestamp, device, "A"))
            carts = (300 if period == 0 else 180) if windows else 200
            orders = (120 if period == 0 else 60) if windows else 80
            if index < carts:
                data["behavior.cart_events"].append(("c" + key, user, timestamp + timedelta(minutes=1), device, "p1"))
            if index < orders:
                data["behavior.order_events"].append(
                    (
                        "o" + key,
                        user,
                        timestamp + timedelta(minutes=2),
                        device,
                        "order" + key,
                        Decimal("100.00"),
                        "paid",
                    )
                )
        data["inventory.stocks"].append((f"s{period}", "p1", "w1", timestamp, 100 - period * 10))
        data["inventory.inbound"].append((f"in{period}", "p1", "w1", timestamp + timedelta(hours=1), 20))
        data["inventory.outbound"].append((f"out{period}", "p1", "w1", timestamp + timedelta(hours=2), 30))
        data["inventory.sales"].append(
            (f"sale{period}", f"out{period}", "p1", "w1", timestamp + timedelta(hours=2), 30)
        )
        data["feedback.reviews"].append(
            (f"r{period}", "p1", "u1", f"o{period}", timestamp, "包装有破损" if period else "体验良好")
        )
        data["feedback.ratings"].append(
            (f"score{period}", f"r{period}", random.choice([2, 3]) if period else 5, timestamp)
        )
        data["feedback.refunds"].append(
            (f"ref{period}", f"r{period}", f"o{period}", Decimal("20.00"), "包装问题", timestamp + timedelta(hours=3))
        )
        for channel in ("A", "B"):
            count = 60 if period == 1 and channel == "B" else 100
            spend = 1500 if period == 1 and channel == "B" else 1000
            # Multiple spend rows ensure queries aggregate before joining orders.
            for campaign in range(2):
                data["market.ad_spend"].append(
                    (
                        f"sp{period}{channel}{campaign}",
                        channel,
                        f"campaign{campaign}",
                        timestamp.date(),
                        Decimal(spend) / 2,
                    )
                )
            for i in range(count):
                data["market.orders"].append(
                    (
                        f"o{period}{channel}{i:03d}",
                        f"u{i:03d}",
                        timestamp - timedelta(minutes=1),
                        timestamp,
                        "paid" if i % 2 else "completed",
                        channel,
                        Decimal("100.00"),
                        "CNY",
                    )
                )
            data["market.sales_summary"].append(
                (f"sum{period}{channel}", channel, timestamp.date(), "CNY", count, Decimal(count * 100))
            )
        data["refund.orders"].append((f"o{period}", "u1", timestamp, Decimal("100.00"), "CNY"))
        for partial in range(2):
            data["refund.requests"].append(
                (
                    f"req{period}{partial}",
                    f"o{period}",
                    "quality",
                    timestamp + timedelta(hours=partial + 1),
                    timestamp + timedelta(days=1),
                    "completed",
                    Decimal("20.00"),
                )
            )
    return data


def quote(value):
    return '"' + value.replace('"', '""') + '"'


async def seed_database(connection):
    async with connection.transaction():
        await connection.execute("SELECT pg_advisory_xact_lock(20260901)")
        for schema, tables in TABLES.items():
            await connection.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
            for table, definition in tables.items():
                await connection.execute(f"CREATE TABLE IF NOT EXISTS {schema}.{table} ({definition})")
        for table, values in rows().items():
            placeholders = ",".join(f"${i + 1}" for i in range(len(values[0])))
            conflict = (
                "(event_id) DO UPDATE SET device=EXCLUDED.device"
                if table in {"behavior.visit_events", "behavior.cart_events", "behavior.order_events"}
                else "DO NOTHING"
            )
            await connection.executemany(f"INSERT INTO {table} VALUES ({placeholders}) ON CONFLICT {conflict}", values)


async def provision(db, database_name="ba_demo"):
    if not re.fullmatch(r"ba_demo|ba_sample_test_[a-f0-9]{12}", database_name):
        raise ValueError("Only the dedicated ba_demo or isolated sample test database may be initialized")
    url = make_url(get_settings().database_url.get_secret_value())
    admin = await asyncpg.connect(url.set(drivername="postgresql").render_as_string(hide_password=False))
    sample = None
    try:
        if not await admin.fetchval("SELECT 1 FROM pg_database WHERE datname=$1", database_name):
            await admin.execute(f"CREATE DATABASE {quote(database_name)}")
        sample = await asyncpg.connect(
            url.set(drivername="postgresql", database=database_name).render_as_string(hide_password=False)
        )
        await seed_database(sample)
        await sample.execute(f"REVOKE ALL ON DATABASE {quote(database_name)} FROM PUBLIC")
        await sample.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        # PUBLIC CONNECT would otherwise let analysis accounts open the application database.
        await admin.execute(f"REVOKE CONNECT ON DATABASE {quote(url.database)} FROM PUBLIC")
        await admin.execute(f"GRANT CONNECT ON DATABASE {quote(url.database)} TO {quote(url.username)}")
        await initialize_runtime(db)
        row = await runtime_row(db, lock=True)
        value = merge_config(row.pending_value or row.config_value, {})
        identities = (await db.scalars(select(DataSource).where(DataSource.database_name == database_name))).all()
        by_schema = {item.schema_name: item for item in identities}
        changed = False
        roles = []
        for schema in TABLES:
            role = f"{database_name}_{schema}_ro"
            roles.append(role)
            identity = by_schema.get(schema)
            existing = value["data_sources"].get(str(identity.id)) if identity else None
            if existing:
                credential = json.loads(await resolve_credential(db, existing["credential_ref"]))
                password = credential["password"]
                if credential["username"] != role:
                    raise ValueError("Existing sample credential does not match its dedicated role")
            else:
                password = random_token()
            if not await admin.fetchval("SELECT 1 FROM pg_roles WHERE rolname=$1", role):
                await admin.execute(
                    f"CREATE ROLE {quote(role)} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD '{password.replace(chr(39), chr(39) * 2)}'"
                )
            elif not existing:
                raise ValueError(
                    "Dedicated role already exists without this application's credential; refusing to reset it"
                )
            await sample.execute(f"GRANT CONNECT ON DATABASE {quote(database_name)} TO {quote(role)}")
            await sample.execute(f"GRANT USAGE ON SCHEMA {schema} TO {quote(role)}")
            await sample.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO {quote(role)}")
            if not identity:
                identity = DataSource(
                    connection_target={"host": url.host, "port": url.port or 5432},
                    database_name=database_name,
                    schema_name=schema,
                )
                db.add(identity)
                await db.flush()
            if not existing:
                reference = await store_credential(db, json.dumps({"username": role, "password": password}))
                value["data_sources"][str(identity.id)] = {
                    "name": NAMES[schema],
                    "credential_ref": reference,
                    "status": "enabled",
                }
                changed = True
        if changed:
            save_draft(row, await validate_bundle(db, value, row.config_value))
            record_event(
                db,
                "sample_data_prepared",
                operator_id=None,
                target_type="database",
                target_id=database_name,
                seed=SEED,
                version=row.pending_version,
            )
        await db.commit()
        counts = {table: await sample.fetchval(f"SELECT count(*) FROM {table}") for table in rows()}
        return {
            "database": database_name,
            "seed": SEED,
            "counts": counts,
            "pending_version": row.pending_version,
            "roles": roles,
        }
    finally:
        if sample:
            await sample.close()
        await admin.close()


async def main(database_name):
    async with Session() as db:
        result = await provision(db, database_name)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("样例数据已准备。请在管理页测试并重载整份草稿；重复导入保留已有记录与有效配置。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="ba_demo")
    asyncio.run(main(parser.parse_args().database))
