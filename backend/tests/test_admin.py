import asyncio
import json
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_auth import execute, login


@pytest.fixture(scope="module")
def samples(environment):
    from app.sample_data import provision

    name = "ba_sample_test_" + uuid.uuid4().hex[:12]

    async def prepare():
        engine = create_async_engine(environment["url"])
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                first = await provision(db, name)
                sample = await asyncpg.connect(
                    environment["url"].set(drivername="postgresql", database=name).render_as_string(hide_password=False)
                )
                try:
                    await sample.execute("UPDATE behavior.visit_events SET device='obsolete_device'")
                finally:
                    await sample.close()
                second = await provision(db, name)
                assert first == second
                assert len(first["counts"]) == 25 and all(first["counts"].values())
                return first
        finally:
            await engine.dispose()

    result = asyncio.run(prepare())
    try:
        yield result
    finally:

        async def cleanup():
            connection = await asyncpg.connect(
                environment["url"].set(drivername="postgresql").render_as_string(hide_password=False)
            )
            try:
                assert name.startswith("ba_sample_test_") and len(name) == 27
                await connection.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
                for role in result["roles"]:
                    assert role.startswith(name + "_")
                    await connection.execute(f'DROP ROLE "{role}"')
            finally:
                await connection.close()

        asyncio.run(cleanup())


def headers(user, environment):
    return {"Origin": environment["app"], "X-CSRF-Token": user["csrf_token"]}


def test_admin_permissions_and_csrf(client, environment):
    user, _, _ = login(client, environment)
    for path in ("/api/admin/configs", "/api/admin/logs", "/api/data-sources?view=admin"):
        assert client.get(environment["app"] + path).status_code == 403
    for path in (
        "/api/admin/configs",
        "/api/admin/reload",
        "/api/admin/data-sources",
        "/api/admin/data-sources/1/test",
    ):
        assert (
            client.post(
                environment["app"] + path,
                json={"expected_version": 1, "changes": {}},
                headers=headers(user, environment),
            ).status_code
            == 403
        )
    admin, _, _ = login(client, environment, "admin_demo")
    assert client.post(environment["app"] + "/api/admin/reload", json={"expected_version": 1}).status_code == 403
    assert client.get(environment["app"] + "/api/admin/configs").json()["active_version"] == 1


def test_samples_isolation_queries_and_publication(client, environment, samples):
    from app.core.config import ROOT

    user, _, _ = login(client, environment, "admin_demo")
    base = environment["app"]
    sources = client.get(base + "/api/data-sources?view=admin").json()["items"]
    assert len(sources) == 6 and all(source["effective_status"] == "not_published" for source in sources)
    assert client.get(base + "/api/data-sources").json()["items"] == []
    for source in sources:
        result = client.post(base + f"/api/admin/data-sources/{source['id']}/test", headers=headers(user, environment))
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "success", result.text
        assert all(result.json()["checks"].values())
        assert result.json()["tested_version"] == samples["pending_version"]
    result = client.post(
        base + "/api/admin/reload",
        json={"expected_version": samples["pending_version"]},
        headers=headers(user, environment),
    )
    assert result.status_code == 200, result.text
    published = client.get(base + "/api/data-sources").json()["items"]
    assert len(published) == 6
    assert all(item["database"] == samples["database"] and item["schema"] for item in published)
    assert all("credential_ref" not in item and "connection_target" not in item for item in published)

    async def query():
        connection = await asyncpg.connect(
            environment["url"]
            .set(drivername="postgresql", database=samples["database"])
            .render_as_string(hide_password=False)
        )
        try:
            behavior = await connection.fetch((ROOT / "samples/queries/behavior.sql").read_text(encoding="utf-8"))
            assert [tuple(row.values()) for row in behavior] == [
                ("base", "macos", 400, 200, 80),
                ("base", "windows", 600, 300, 120),
                ("current", "macos", 400, 200, 80),
                ("current", "windows", 600, 180, 60),
            ]
            market = await connection.fetch((ROOT / "samples/queries/market.sql").read_text(encoding="utf-8"))
            assert [
                (row["orders"], row["revenue"], row["spend"], row["roas"], row["acquisition_cost"]) for row in market
            ] == [
                (100, 10000, 1000, 10, 10),
                (100, 10000, 1000, 10, 10),
                (100, 10000, 1000, 10, 10),
                (60, 6000, 1500, 4, 25),
            ]
            summary = await connection.fetch(
                "SELECT order_count,revenue FROM market.sales_summary ORDER BY period_start,channel_id"
            )
            assert [(r["order_count"], r["revenue"]) for r in summary] == [(r["orders"], r["revenue"]) for r in market]
            for statement in (ROOT / "samples/queries/other-scenarios.sql").read_text(encoding="utf-8").split(";"):
                if statement.strip():
                    assert await connection.fetch(statement)
        finally:
            await connection.close()

    asyncio.run(query())


def test_config_concurrency_validation_reload_and_logs(client, environment, samples):
    user, _, _ = login(client, environment, "admin_demo")
    base = environment["app"]
    state = client.get(base + "/api/admin/configs").json()
    initial = state["active_version"]
    request_headers = headers(user, environment)
    changes = {"expected_version": state["edit_version"], "changes": {"tools": {"sql_timeout_seconds": 29}}}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: client.post(base + "/api/admin/configs", json=changes, headers=request_headers).status_code,
                range(2),
            )
        )
    assert sorted(results) == [200, 409]
    state = client.get(base + "/api/admin/configs").json()
    assert state["active_version"] == initial and state["pending_version"] == initial + 1
    for invalid in (
        {"parse_limits": {"parse_max_rows": 100001}},
        {"context": {"context_window_tokens": 1000}},
        {"data_sources": {"999999": {"name": "bad", "status": "enabled", "credential_ref": "missing"}}},
        {"auth": {"callback_url": "https://other.invalid/auth/callback"}},
    ):
        response = client.post(
            base + "/api/admin/configs",
            json={"expected_version": state["edit_version"], "changes": invalid},
            headers=request_headers,
        )
        assert response.status_code == 400, response.text
    source = client.get(base + "/api/data-sources?view=admin").json()["items"][0]
    response = client.post(
        base + "/api/admin/data-sources",
        json={
            "expected_version": state["edit_version"],
            "id": source["id"],
            "name": "bad",
            "status": "enabled",
            "schema_name": "other",
        },
        headers=request_headers,
    )
    assert response.status_code == 409 and response.json()["code"] == "DATA_SOURCE_SCOPE_LOCKED"
    secret = "m02-synthetic-secret-never-echo"
    response = client.post(
        base + "/api/admin/configs",
        json={
            "expected_version": state["edit_version"],
            "changes": {
                "model": {"api_url": "https://model.example.invalid/v1", "model_id": "synthetic-model"},
                "context": {"context_window_tokens": 32768},
            },
            "credentials": {"model": secret},
        },
        headers=request_headers,
    )
    assert response.status_code == 200 and secret not in response.text
    state = response.json()
    stored = execute(environment, "SELECT config_value::text FROM system_configs WHERE config_group='credentials'")
    assert stored and all(secret not in row["config_value"] for row in stored)
    logs = client.get(
        base + "/api/admin/logs",
        params={"scope": "system", "operator_id": user["id"], "target_type": "config", "limit": 1},
    ).json()
    assert len(logs["items"]) == 1 and logs["next_cursor"]
    older = client.get(
        base + "/api/admin/logs",
        params={
            "scope": "system",
            "operator_id": user["id"],
            "target_type": "config",
            "limit": 1,
            "cursor": logs["next_cursor"],
        },
    ).json()
    assert older["items"][0]["id"] != logs["items"][0]["id"]
    assert all(item["operator_id"] == user["id"] and item["task_id"] is None for item in logs["items"])
    assert secret not in client.get(base + "/api/admin/logs").text
    assert client.get(base + "/api/admin/logs?cursor=bad").status_code == 400
    # An invalid persisted draft must not partially publish or disappear.
    good = state["pending_config"]
    bad = {**good, "context": {**good["context"], "context_window_tokens": 1}}
    execute(
        environment, "UPDATE system_configs SET pending_value=$1::jsonb WHERE config_group='runtime'", json.dumps(bad)
    )
    response = client.post(
        base + "/api/admin/reload", json={"expected_version": state["edit_version"]}, headers=request_headers
    )
    assert response.status_code == 400
    failed = client.get(base + "/api/admin/configs")
    # The inspection endpoint must still expose a repairable raw draft, even when validation failed.
    assert failed.status_code == 200
    execute(
        environment, "UPDATE system_configs SET pending_value=$1::jsonb WHERE config_group='runtime'", json.dumps(good)
    )
    result = client.post(
        base + "/api/admin/reload", json={"expected_version": state["edit_version"]}, headers=request_headers
    )
    assert result.status_code == 200, result.text
    assert result.json()["active_version"] == state["edit_version"]
    no_change = client.post(
        base + "/api/admin/reload", json={"expected_version": state["edit_version"]}, headers=request_headers
    )
    assert no_change.json()["status"] == "no_change"
    # Restart preserves the draft and effective version; no implicit publication.
    result = client.post(
        base + "/api/admin/configs",
        json={"expected_version": state["edit_version"], "changes": {"tools": {"sql_timeout_seconds": 28}}},
        headers=request_headers,
    )
    expected = result.json()
    environment["processes"]["backend"].terminate()
    environment["processes"]["backend"].wait(timeout=10)
    environment["start"](
        "backend",
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "18010", "--no-access-log"],
    )
    import time

    import httpx2

    for _ in range(40):
        try:
            if client.get(base + "/health/ready").status_code == 200:
                break
        except httpx2.HTTPError:
            pass
        time.sleep(0.25)
    assert client.get(base + "/api/admin/configs").json() == expected
    # Restore unconfigured model so later tests do not mistake this fixture for a real model.
    response = client.post(
        base + "/api/admin/configs",
        json={"expected_version": expected["edit_version"], "changes": {"model": {"credential_ref": None}}},
        headers=request_headers,
    )
    assert response.status_code == 200


def test_execution_snapshot_contract(environment, samples):
    from copy import deepcopy

    from app.core.errors import ApiError
    from app.runtime import execution_config, runtime_row

    async def check():
        engine = create_async_engine(environment["url"])
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                row = await runtime_row(db, lock=True)
                value = deepcopy(row.config_value)
                identifier = next(iter(value["data_sources"]))
                value["model"].update(
                    api_url="https://model.example.invalid/v1", model_id="fixture", credential_ref="fixture-reference"
                )
                value["context"]["context_window_tokens"] = 32768
                row.config_value = value
                version, snapshot = await execution_config(db, identifier)
                assert version == row.config_version
                assert list(snapshot["data_sources"]) == [identifier]
                assert snapshot["data_sources"][identifier]["schema_name"] in {
                    table.split(".")[0] for table in samples["counts"]
                }
                updated = deepcopy(value)
                updated["tools"]["sql_enabled"] = False
                updated["data_sources"][identifier]["status"] = "disabled"
                row.config_value = updated
                row.config_version += 1
                with pytest.raises(ApiError) as error:
                    await execution_config(db, identifier)
                assert error.value.code == "DATA_SOURCE_UNAVAILABLE"
                assert snapshot["tools"]["sql_enabled"] is True
                assert snapshot["data_sources"][identifier]["status"] == "enabled"
                assert version != row.config_version
                updated = deepcopy(value)
                updated["model"]["credential_ref"] = None
                row.config_value = updated
                with pytest.raises(ApiError) as error:
                    await execution_config(db, identifier)
                assert error.value.code == "MODEL_NOT_CONFIGURED"
                await db.rollback()
        finally:
            await engine.dispose()

    asyncio.run(check())


def test_removed_source_retains_identity(client, environment, samples):
    user, _, _ = login(client, environment, "admin_demo")
    base = environment["app"]
    state = client.get(base + "/api/admin/configs").json()
    source = client.get(base + "/api/data-sources?view=admin").json()["items"][0]
    response = client.request(
        "DELETE",
        base + f"/api/admin/data-sources/{source['id']}",
        json={"expected_version": state["edit_version"]},
        headers=headers(user, environment),
    )
    assert response.status_code == 200
    assert len(client.get(base + "/api/data-sources").json()["items"]) == 6
    result = client.post(
        base + "/api/admin/reload",
        json={"expected_version": response.json()["pending_version"]},
        headers=headers(user, environment),
    )
    assert result.status_code == 200, result.text
    assert len(client.get(base + "/api/data-sources").json()["items"]) == 5
    assert execute(environment, "SELECT id FROM data_sources WHERE id=$1", int(source["id"]))
