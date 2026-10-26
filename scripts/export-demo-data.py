"""Export the two demo datasets from the application's deterministic seed."""

import csv
import io
import sys
from datetime import datetime
from pathlib import Path

import sqlglot
from sqlglot import exp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.sample_data import TABLES, rows  # noqa: E402


def outputs():
    dataset = rows()
    for schema in ("behavior", "market"):
        for table, definition in TABLES[schema].items():
            ddl = sqlglot.parse_one(f"CREATE TABLE {schema}.{table} ({definition})", read="postgres")
            columns = [column.name for column in ddl.this.expressions if isinstance(column, exp.ColumnDef)]
            values = dataset[f"{schema}.{table}"]
            assert columns and all(len(row) == len(columns) for row in values), "Seed row does not match its schema"
            output = io.StringIO(newline="")
            writer = csv.writer(output, lineterminator="\n")
            writer.writerow(columns)
            for row in values:
                writer.writerow(
                    "\\N" if value is None else value.isoformat() if isinstance(value, datetime) else value
                    for value in row
                )
            yield ROOT / "samples/demos/data" / f"{schema}.{table}.csv", output.getvalue()


if __name__ == "__main__":
    for path, content in outputs():
        if "--check" in sys.argv:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                raise SystemExit(f"Demo data export out of date: {path.name}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
    print("Eight demo CSV tables match the application seed; no database writes or model calls.")
