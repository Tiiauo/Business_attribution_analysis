import asyncio
import json
from datetime import date, datetime
from decimal import Decimal

import asyncpg
from test_admin import samples  # noqa: F401,F811


def test_independent_funnel_and_payment_boundaries(environment, samples):  # noqa: F811
    from app.core.config import ROOT

    async def check():
        connection = await asyncpg.connect(
            environment["url"]
            .set(drivername="postgresql", database=samples["database"])
            .render_as_string(hide_password=False)
        )
        transaction = connection.transaction()
        await transaction.start()
        try:
            fixtures = json.loads((ROOT / "samples/boundaries.json").read_text(encoding="utf-8"))
            await connection.execute("CREATE SCHEMA boundary")
            for schema in ("behavior", "market"):
                for table, values in fixtures[schema].items():
                    if table == "expected":
                        continue
                    await connection.execute(
                        f"CREATE TABLE boundary.{table} AS SELECT * FROM {schema}.{table} WITH NO DATA"
                    )
                    normalized = []
                    for row in values:
                        normalized.append(
                            tuple(
                                datetime.fromisoformat(v)
                                if isinstance(v, str) and "T" in v
                                else date.fromisoformat(v)
                                if isinstance(v, str) and v.startswith("2026-")
                                else Decimal(v)
                                if isinstance(v, str) and v.endswith(".00")
                                else v
                                for v in row
                            )
                        )
                    args = ",".join(f"${i + 1}" for i in range(len(normalized[0])))
                    await connection.executemany(f"INSERT INTO boundary.{table} VALUES ({args})", normalized)
            behavior_sql = (
                (ROOT / "samples/queries/behavior.sql").read_text(encoding="utf-8").replace("behavior.", "boundary.")
            )
            result = await connection.fetch(behavior_sql)
            assert [list(row.values()) for row in result] == fixtures["behavior"]["expected"]
            market_sql = (
                (ROOT / "samples/queries/market.sql").read_text(encoding="utf-8").replace("market.", "boundary.")
            )
            quality_sql = (
                (ROOT / "samples/queries/market-quality.sql")
                .read_text(encoding="utf-8")
                .replace("market.", "boundary.")
            )
            quality = await connection.fetchrow(quality_sql)
            assert quality["conflicting_orders"] == 0 and quality["missing_paid_at"] == 1
            result = await connection.fetch(market_sql)
            assert (
                len(result) == 1
                and result[0]["orders"] == 1
                and result[0]["revenue"] == 100
                and result[0]["spend"] == 100
            )
            await connection.execute("UPDATE boundary.orders SET channel_id=NULL WHERE order_id='cross-paid'")
            await connection.execute("INSERT INTO boundary.ad_spend VALUES ('c1','C','campaign-3','2026-09-08',50)")
            variant = {row["channel_id"]: row for row in await connection.fetch(market_sql)}
            assert variant["未归因"]["orders"] == 1 and variant["未归因"]["revenue"] == 100
            assert variant["未归因"]["roas"] is None
            for channel in ("B", "C"):
                assert variant[channel]["orders"] == 0 and variant[channel]["revenue"] == 0
                assert variant[channel]["roas"] == 0 and variant[channel]["acquisition_cost"] is None
            await connection.execute(
                "UPDATE boundary.orders SET paid_amount=200 WHERE ctid=(SELECT max(ctid) FROM boundary.orders WHERE order_id='cross-paid')"
            )
            assert (await connection.fetchrow(quality_sql))["conflicting_orders"] == 1
            # Boundary schemas exist only in this rolled-back transaction.
        finally:
            await transaction.rollback()
            await connection.close()

    asyncio.run(check())
