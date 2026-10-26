"""Read-only inventory used inside the source and restored backup containers."""

import asyncio
import hashlib
import json
import sys
import traceback
from pathlib import Path

import asyncpg
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization


def digest(value):
    return hashlib.sha256(value).hexdigest()


def identifier(value):
    return '"' + value.replace('"', '""') + '"'


async def main():
    secrets = Path("/snapshot/secrets")
    password = (secrets / "postgres.password").read_text().strip()
    cipher = Fernet((secrets / "master.key").read_bytes().strip())
    assert cipher.decrypt((secrets / "oidc-client.secret").read_bytes().strip())
    serialization.load_pem_private_key((secrets / "idp.pem").read_bytes(), password=None)
    options = dict(host="db", user="ba_app", password=password)
    admin = await asyncpg.connect(**options, database="postgres")
    try:
        databases = await admin.fetch(
            "SELECT datname FROM pg_database WHERE NOT datistemplate AND datname<>'restore_control' ORDER BY datname"
        )
        roles = await admin.fetch(
            "SELECT rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,rolreplication,rolbypassrls,rolpassword FROM pg_authid WHERE rolname<>'ba_restore' ORDER BY rolname"
        )
        result = {
            "databases": {},
            "role_digest": digest(json.dumps([list(r) for r in roles], default=str).encode()),
            "files": {},
            "credentials_decrypted": 0,
            "source_logins": 0,
        }
        for entry in databases:
            name = entry["datname"]
            db = await asyncpg.connect(**options, database=name)
            try:
                tables = await db.fetch(
                    "SELECT schemaname,tablename FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema') ORDER BY 1,2"
                )
                inventory = {}
                for table in tables:
                    target = identifier(table["schemaname"]) + "." + identifier(table["tablename"])
                    rows = await db.fetch(
                        "SELECT row_to_json(t)::text AS value FROM " + target + " t ORDER BY row_to_json(t)::text"
                    )
                    inventory[target] = {
                        "rows": len(rows),
                        "sha256": digest("\n".join(row["value"] for row in rows).encode()),
                    }
                result["databases"][name] = inventory
                if name == "ba_compose":
                    credentials = {}
                    for row in await db.fetch(
                        "SELECT config_key,config_value FROM system_configs WHERE config_group='credentials'"
                    ):
                        value = json.loads(row["config_value"])
                        credentials[row["config_key"]] = cipher.decrypt(value["encrypted"].encode()).decode()
                    result["credentials_decrypted"] = len(credentials)
                    runtime = await db.fetchval(
                        "SELECT config_value FROM system_configs WHERE config_group='runtime' AND config_key='runtime_bundle'"
                    )
                    runtime = json.loads(runtime) if runtime is not None else {}
                    for source_id, source in runtime.get("data_sources", {}).items():
                        credential = json.loads(credentials[source["credential_ref"]])
                        database = await db.fetchval(
                            "SELECT database_name FROM data_sources WHERE id=$1", int(source_id)
                        )
                        connection = await asyncpg.connect(
                            host="db", database=database, user=credential["username"], password=credential["password"]
                        )
                        try:
                            assert await connection.fetchval("SELECT 1") == 1
                            assert not await connection.fetchval(
                                "SELECT rolsuper OR rolcreatedb OR rolcreaterole FROM pg_roles WHERE rolname=current_user"
                            )
                            result["source_logins"] += 1
                        finally:
                            await connection.close()
            finally:
                await db.close()
        for folder in ("data", "caddy", "config", "secrets"):
            base = Path("/snapshot") / folder
            result["files"][folder] = {
                path.relative_to(base).as_posix(): digest(path.read_bytes())
                for path in sorted(base.rglob("*"))
                if path.is_file()
            }
        print(json.dumps(result, sort_keys=True))
    finally:
        await admin.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:
        print(
            "BACKUP_PROBE_ERROR",
            type(error).__name__,
            [frame.lineno for frame in traceback.extract_tb(error.__traceback__)],
            file=sys.stderr,
        )
        raise SystemExit(1) from None
