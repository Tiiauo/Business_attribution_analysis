import asyncio
import json
import os
import secrets
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import httpx2
import pytest
import websockets
from test_auth import execute, login
from test_chat import post, sources  # noqa: F401
from test_files import until
from test_tasks import model_config, submit  # noqa: F401,F811

from app.contracts import AnalysisOutput
from app.core.config import ROOT, get_settings
from app.core.errors import ApiError
from app.core.security import require_storage
from app.sandbox import namespace, run_command, run_script
from app.worker import serve
from demo_auth.passwords import hash_password


async def test_parse_restart_barrier_and_late_callback(client, environment):
    from uuid import uuid4

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.attachments import conversation_path
    from app.file_worker import FileWorker

    user, _, _ = login(client, environment)
    cid = post(client, environment, user, "create", {}).json()["id"]
    ids, originals = [], []
    for state in ("pending", "parsing", "success", "parsing"):
        name = uuid4().hex + ".txt"
        original = conversation_path(user["id"], cid, "uploads", name)
        original.parent.mkdir(parents=True, exist_ok=True)
        original.write_text("preserved original")
        originals.append(original)
        aid = (
            await asyncio.to_thread(
                execute,
                environment,
                "INSERT INTO attachments(conversation_id,file_name,file_path,file_type,file_size,parse_status) VALUES($1,$2,$3,'txt',18,$4) RETURNING id",
                int(cid),
                name,
                f"uploads/{user['id']}/{cid}/{name}",
                state,
            )
        )[0]["id"]
        ids.append(aid)
        staging = conversation_path(user["id"], cid, "workspace", "parsing", aid)
        staging.mkdir(parents=True)
        (staging / "partial.txt").write_text("not published")
    await asyncio.to_thread(
        execute, environment, "UPDATE attachments SET delete_status='deleting' WHERE id=$1", ids[-1]
    )
    engine = create_async_engine(environment["url"])
    try:
        files = FileWorker(async_sessionmaker(engine, expire_on_commit=False))
        await files.recover()
        for aid in (ids[0], ids[1], ids[-1]):
            await files.parse(int(cid), aid)
        rows = await asyncio.to_thread(
            execute,
            environment,
            "SELECT id,parse_status,parse_error,delete_status FROM attachments WHERE id=ANY($1::bigint[]) ORDER BY id",
            ids,
        )
        assert [row["parse_status"] for row in rows] == ["failed", "failed", "success", "failed"]
        assert all("PARSE_INTERRUPTED" in row["parse_error"] for row in (rows[0], rows[1], rows[3]))
        assert rows[-1]["delete_status"] == "failed"
        assert all(path.read_text() == "preserved original" for path in originals)
        assert all(
            not conversation_path(user["id"], cid, "workspace", "parsing", aid).exists()
            for aid in (ids[0], ids[1], ids[3])
        )
    finally:
        await engine.dispose()


async def test_real_sandbox_timeout_memory_and_disk_limits():
    with pytest.raises((ApiError, TimeoutError)):
        await run_script("import time\ntime.sleep(10)", {}, "m07-timeout", 1)
    with pytest.raises(ApiError):
        await run_script("x=bytearray(400*1024*1024)\nprint(len(x))", {}, "m07-memory", 15)
    probe = """import errno,json
from pathlib import Path
old=Path('/output/saved.txt');old.write_text('saved evidence')
try:
 Path('/output/too-large.bin').write_bytes(b'x'*(20*1024*1024))
 raise AssertionError('Output capacity was not enforced')
except OSError as e:
 assert e.errno==errno.ENOSPC
assert old.read_text()=='saved evidence'
print(json.dumps({'enospc':True,'existing_preserved':True}))
"""
    result = json.loads(await run_script(probe, {}, "m07-disk", 15))
    assert result == {"enospc": True, "existing_preserved": True}


async def test_browser_periodic_snapshot_recovers_dropped_events(environment, model_config):  # noqa: F811
    async def controlled(context):
        await context.append("已保存的第一段。")
        await asyncio.sleep(1)
        await context.append("已保存的第二段。")
        return AnalysisOutput(
            problem_definition="M07 lost events fixture",
            key_metrics=[],
            evidence_list=[],
            conclusion_text="证据不足",
            missing_data_text="无模型数据",
            next_action_text="1. 补充数据\n2. 验证口径",
        )

    worker = asyncio.create_task(serve(controlled))
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["node", str(ROOT / "frontend/node_modules/@playwright/test/cli.js"), "test", "m07.spec.ts"],
            cwd=ROOT / "frontend",
            env={
                **os.environ,
                "BA_UI_URL": environment["app"],
                "BA_UI_OUTPUT": str(environment["work"] / "m07-browser"),
            },
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=100,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


async def test_invalid_sql_is_correctable_without_masking_infrastructure_errors(environment, monkeypatch):
    from types import SimpleNamespace

    import asyncpg

    from app.analysis_tools import AnalysisTools

    tools = object.__new__(AnalysisTools)
    tools.source = {"schema_name": "market"}

    async def connect():
        return await asyncpg.connect(
            environment["url"].set(drivername="postgresql").render_as_string(hide_password=False)
        )

    monkeypatch.setattr(tools, "connect", connect)
    with pytest.raises(ApiError) as caught:
        await tools.database(SimpleNamespace(query="SELECT missing_column", filter_summary="invalid fixture"))
    assert caught.value.code == "SQL_INVALID_QUERY"

    async def unavailable():
        raise OSError("synthetic connection failure")

    monkeypatch.setattr(tools, "connect", unavailable)
    with pytest.raises(ApiError) as caught:
        await tools.database(SimpleNamespace(query="SELECT 1", filter_summary="connection fixture"))
    assert caught.value.code == "SQL_EXECUTION_FAILED"


def test_storage_refusal_preserves_existing_files(tmp_path, monkeypatch):
    from collections import namedtuple

    import app.core.security as security

    existing = tmp_path / "report.md"
    existing.write_text("saved evidence")
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(security.shutil, "disk_usage", lambda _: usage(10 * 1024**3, 6 * 1024**3, 4 * 1024**3))
    with pytest.raises(ApiError) as caught:
        require_storage()
    assert caught.value.status == 507 and caught.value.code == "INSUFFICIENT_STORAGE"
    assert existing.read_text() == "saved evidence"
    existing.unlink()
    assert not existing.exists()


def test_sandbox_namespace_separates_compose_projects(tmp_path, monkeypatch):
    settings = get_settings()
    first = namespace()
    key = tmp_path / "other-master.key"
    key.write_bytes(b"independent acceptance environment")
    monkeypatch.setattr(settings, "master_key_file", key)
    assert namespace() != first


def test_only_deployment_internal_oidc_url_can_use_http(monkeypatch):
    from pydantic import ValidationError

    from app.runtime import RuntimeBundle

    settings = get_settings()
    monkeypatch.setattr(settings, "issuer_internal_url", "http://idp:8000")
    value = {
        "auth": {
            "issuer_url": settings.issuer_url,
            "issuer_internal_url": "http://idp:8000",
            "client_id": settings.client_id,
            "callback_url": settings.callback_url,
            "credential_ref": "oidc:bootstrap",
            "role_claim": "roles",
            "role_mapping": {"analyst": "analyst"},
        }
    }
    assert RuntimeBundle.model_validate(value)
    with pytest.raises(ValidationError):
        RuntimeBundle.model_validate({**value, "model": {"api_url": "http://idp:8000"}})


async def test_ten_users_real_websockets_three_workers_and_isolation(environment, model_config, tmp_path, monkeypatch):  # noqa: F811
    password = secrets.token_urlsafe(24)
    accounts = [
        {"username": f"m07_user_{i}", "display_name": f"M07 analyst {i}", "role": "analyst", "password": password}
        for i in range(10)
    ]
    account_file = tmp_path / "accounts.json"
    account_file.write_text(json.dumps(accounts), encoding="utf-8")
    monkeypatch.setattr(get_settings(), "demo_accounts_file", account_file)
    encoded = hash_password(password)
    for account in accounts:
        await asyncio.to_thread(
            execute,
            environment,
            "INSERT INTO demo_identities(username,display_name,role,password_hash) VALUES($1,$2,'analyst',$3)",
            account["username"],
            account["display_name"],
            encoded,
        )

    def connect_user(account):
        client = httpx2.Client(verify=environment["tls"], timeout=20)
        user, _, _ = login(client, environment, account["username"])
        cid = post(client, environment, user, "create", {"data_source_id": model_config[0]}).json()["id"]
        return client, user, cid

    with ThreadPoolExecutor(max_workers=10) as pool:
        users = list(pool.map(connect_user, accounts))
    assert len({user["id"] for _, user, _ in users}) == 10
    sockets, ids, started = [], [], []
    gate = asyncio.Event()

    async def controlled(context):
        started.append(str(context.task_id))
        await context.append("M07 concurrency fixture; real isolated computation")
        await context.tool_start("run_command", "stats", {"command": "python_stats"})
        value = await run_command("python_stats", {"values": [10, 20, 30]}, context.task_id, 30)
        assert json.loads(value)["sum"] == "60"
        await context.tool_finish("stats", value)
        await gate.wait()
        return AnalysisOutput(
            problem_definition="M07 concurrency fixture",
            key_metrics=[],
            evidence_list=[],
            conclusion_text="Scheduler verification with a real sandbox, not a model demonstration.",
            missing_data_text="No model conclusion in this scheduler test.",
            next_action_text="1. Verify model demos\n2. Verify persistence",
        )

    worker = asyncio.create_task(serve(controlled))
    try:
        for client, user, cid in users:
            token = (
                await asyncio.to_thread(post, client, environment, user, "ws-token", {"conversation_id": cid})
            ).json()["websocket_token"]
            ws = await websockets.connect(
                environment["app"].replace("https:", "wss:")
                + f"/api/chat/ws/chat?conversation_id={cid}&websocket_token={token}",
                ssl=environment["tls"],
                origin=environment["app"],
                additional_headers={"Cookie": "ba_session=" + client.cookies.get("ba_session")},
            )
            assert json.loads(await ws.recv())["type"] == "subscription_ready"
            sockets.append(ws)
        for index, (client, user, cid) in enumerate(users):
            response = await asyncio.to_thread(
                submit, client, environment, user, cid, model_config[0], client_request_id=f"m07-{index}"
            )
            assert response.status_code == 200, response.text
            ids.append(response.json()["task_id"])

        async def states():
            return await asyncio.to_thread(
                execute,
                environment,
                "SELECT id,task_status FROM analysis_tasks WHERE id=ANY($1::bigint[]) ORDER BY id",
                list(map(int, ids)),
            )

        await until(lambda: len(started) == 3)
        rows = await states()
        assert sum(row["task_status"] == "running" for row in rows) == 3
        assert sum(row["task_status"] == "queued" for row in rows) == 7
        health = await asyncio.to_thread(
            subprocess.run, [sys.executable, "-m", "app.worker", "--check"], cwd=ROOT, capture_output=True, timeout=10
        )
        assert health.returncode == 0
        for index, (client, user, cid) in enumerate(users):
            foreign = (index + 1) % 10
            assert (
                await asyncio.to_thread(client.get, environment["app"] + "/api/chat/ls/" + users[foreign][2])
            ).status_code == 404
            assert (
                await asyncio.to_thread(client.get, environment["app"] + "/api/tasks/" + ids[foreign])
            ).status_code == 404
            assert (
                await asyncio.to_thread(
                    post, client, environment, user, "ws-token", {"conversation_id": users[foreign][2]}
                )
            ).status_code == 404
            assert (
                await asyncio.to_thread(
                    submit, client, environment, user, cid, model_config[0], client_request_id="second"
                )
            ).json()["code"] == "CONVERSATION_BUSY"
        gate.set()
        async with asyncio.timeout(120):
            while True:
                rows = await states()
                assert sum(row["task_status"] == "running" for row in rows) <= 3
                if all(row["task_status"] == "success" for row in rows):
                    break
                assert not any(row["task_status"] == "failed" for row in rows), rows
                await asyncio.sleep(0.1)
        assert len(started) == len(set(started)) == 10
        for ws, tid in zip(sockets, ids, strict=True):
            event_ids = []
            async with asyncio.timeout(10):
                while True:
                    value = json.loads(await ws.recv())
                    assert value["task_id"] == tid
                    event_ids.append(value["event_id"])
                    if value["type"] == "done":
                        break
            assert event_ids == list(range(1, event_ids[-1] + 1))
    finally:
        gate.set()
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        if ids:
            await asyncio.to_thread(
                execute,
                environment,
                "UPDATE analysis_tasks SET task_status='cancelled',finished_at=now() WHERE id=ANY($1::bigint[]) AND task_status IN ('queued','running')",
                list(map(int, ids)),
            )
        for ws in sockets:
            await ws.close()
        for client, _, _ in users:
            client.close()
