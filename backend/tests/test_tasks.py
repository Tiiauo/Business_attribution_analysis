import asyncio
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from test_auth import execute, login
from test_chat import post, sources  # noqa: F401, F811


@pytest.fixture
def model_config(environment, sources):  # noqa: F811
    original = execute(
        environment,
        "SELECT config_value,pending_value,pending_version FROM system_configs WHERE config_group='runtime'",
    )[0]
    value = json.loads(original["config_value"])
    value["model"].update(
        api_url="https://model.invalid/v1", model_id="controlled-test", credential_ref="oidc:bootstrap"
    )
    value["context"]["context_window_tokens"] = 65536
    execute(
        environment,
        "UPDATE system_configs SET config_value=$1::jsonb,pending_value=NULL,pending_version=NULL WHERE config_group='runtime'",
        json.dumps(value),
    )
    yield sources
    execute(
        environment,
        "UPDATE system_configs SET config_value=$1::jsonb,pending_value=$2::jsonb,pending_version=$3 WHERE config_group='runtime'",
        original["config_value"],
        original["pending_value"],
        original["pending_version"],
    )


def submit(client, environment, user, cid, source, **kwargs):
    return post(
        client,
        environment,
        user,
        "send",
        {
            "conversation_id": cid,
            "data_source_id": source,
            "client_request_id": "same-request",
            "content": "调查收入变化",
            **kwargs,
        },
    )


def config_value(environment):
    return json.loads(
        execute(environment, "SELECT config_value FROM system_configs WHERE config_group='runtime'")[0]["config_value"]
    )


def save_config(environment, value):
    execute(
        environment,
        "UPDATE system_configs SET config_value=$1::jsonb WHERE config_group='runtime'",
        json.dumps(value),
    )


def task_post(client, environment, user, path):
    return client.post(
        environment["app"] + "/api/tasks/" + path,
        json={},
        headers={"Origin": environment["app"], "X-CSRF-Token": user["csrf_token"]},
    )


def test_send_atomic_idempotency_guards_and_snapshot(client, environment, model_config):
    from app.contracts import TaskSnapshot

    user, _, _ = login(client, environment)
    source = model_config[0]
    cid = post(client, environment, user, "create", {}).json()["id"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(lambda _: submit(client, environment, user, cid, source), range(4)))
    assert all(response.status_code == 200 for response in replies), [r.text for r in replies]
    assert len({r.json()["task_id"] for r in replies}) == 1
    accepted = replies[0].json()
    tid = accepted["task_id"]
    assert accepted["title"] == "调查收入变化" and accepted["data_source_locked"]
    snapshot = client.get(environment["app"] + "/api/tasks/" + tid).json()
    TaskSnapshot.model_validate(snapshot)
    assert (
        snapshot["last_event_id"] == 1 and snapshot["assistant_message_id"] is None and len(snapshot["messages"]) == 1
    )
    assert submit(client, environment, user, cid, source, content="changed").json()["code"] == "IDEMPOTENCY_CONFLICT"
    assert (
        submit(client, environment, user, cid, source, client_request_id="another").json()["code"]
        == "CONVERSATION_BUSY"
    )
    assert task_post(client, environment, user, tid + "/cancel").json()["task_status"] == "cancelled"
    timestamp = client.get(environment["app"] + "/api/tasks/" + tid).json()["cancel_requested_at"]
    assert (
        task_post(client, environment, user, tid + "/cancel").json()["cancel_requested_at"].replace("+00:00", "Z")
        == timestamp
    )
    assert client.get(environment["app"] + "/api/results/" + tid).json()["code"] == "TASK_CANCELLED"
    execute(environment, "UPDATE conversations SET status='archived' WHERE id=$1", int(cid))
    assert submit(client, environment, user, cid, source).json()["task_id"] == tid
    assert (
        submit(client, environment, user, cid, source, client_request_id="new").json()["code"]
        == "CONVERSATION_ARCHIVED"
    )
    other, _, _ = login(client, environment, "analyst_b")
    assert client.get(environment["app"] + "/api/tasks/" + tid).status_code == 404
    assert task_post(client, environment, other, tid + "/cancel").status_code == 404
    assert client.get(environment["app"] + "/api/results/" + tid).status_code == 404


@pytest.mark.asyncio
async def test_queue_three_fifo_cancel_prefix_and_restart(client, environment, model_config):
    from app.contracts import AnalysisOutput
    from app.worker import serve

    user, _, _ = login(client, environment)
    ids = []
    for i in range(5):
        cid = post(client, environment, user, "create", {"data_source_id": model_config[0]}).json()["id"]
        ids.append(
            submit(client, environment, user, cid, model_config[0], client_request_id=f"queue-{i}").json()["task_id"]
        )
    started, stop = [], asyncio.Event()

    async def controlled(context):
        started.append(str(context.task_id))
        await context.append("已持久化的前缀")
        await stop.wait()
        return AnalysisOutput(
            problem_definition="controlled fixture",
            key_metrics=[],
            evidence_list=[],
            conclusion_text="证据不足",
            missing_data_text="测试执行器不调用模型",
            next_action_text="1. 检查数据\n2. 验证假设",
        )

    worker = asyncio.create_task(serve(controlled))

    async def rows():
        return await asyncio.to_thread(
            execute,
            environment,
            "SELECT id,task_status,cancel_requested_at FROM analysis_tasks WHERE id=ANY($1::bigint[]) ORDER BY id",
            list(map(int, ids)),
        )

    async def until(predicate):
        async with asyncio.timeout(15):
            while not predicate(await rows()):
                await asyncio.sleep(0.1)

    try:
        await until(lambda values: sum(r["task_status"] == "running" for r in values) == 3)
        assert started == ids[:3]
        response = task_post(client, environment, user, ids[3] + "/cancel").json()
        assert response["task_status"] == "cancelled" and ids[3] not in started
        response = task_post(client, environment, user, ids[0] + "/cancel").json()
        assert response["is_cancelling"] and response["task_status"] == "running"
        await until(lambda values: values[0]["task_status"] == "cancelled" and values[4]["task_status"] == "running")
        snapshot = client.get(environment["app"] + "/api/tasks/" + ids[0]).json()
        assert snapshot["messages"][-1]["content"] == "已持久化的前缀"
        assert snapshot["messages"][-1]["message_status"] == "cancelled"
        assert started == ids[:3] + ids[4:]
        stop.set()
        await until(lambda values: all(r["task_status"] in {"success", "cancelled"} for r in values))
        result = client.get(environment["app"] + "/api/results/" + ids[1]).json()
        assert (
            result["problem_definition"] == "controlled fixture"
            and result["export_status"]["html"]["status"] == "not_generated"
        )
        assert task_post(client, environment, user, ids[1] + "/cancel").json()["task_status"] == "success"
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


def test_send_validation_and_final_scope(client, environment, model_config):
    user, _, _ = login(client, environment)
    source = model_config[0]
    cid = post(client, environment, user, "create", {}).json()["id"]
    for content, code in ((" ", "EMPTY_MESSAGE"), ("😀" * 20001, "FIELD_TOO_LONG")):
        assert submit(client, environment, user, cid, source, content=content).json()["code"] == code
    assert submit(client, environment, user, cid, source, data_source_id="oops").status_code == 400
    assert (
        submit(client, environment, user, cid, source, task_kind="final_report", attachment_ids=["1"]).json()["code"]
        == "FINAL_REPORT_ATTACHMENTS_NOT_ALLOWED"
    )
    assert (
        submit(client, environment, user, cid, source, task_kind="final_report").json()["code"]
        == "FINAL_REPORT_NO_BASE"
    )
    assert not execute(environment, "SELECT id FROM messages WHERE conversation_id=$1", int(cid))


@pytest.mark.asyncio
async def test_queued_task_revalidates_source_and_user(client, environment, model_config):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.worker import claim

    user, _, _ = login(client, environment)
    source = model_config[0]
    original = await asyncio.to_thread(config_value, environment)
    cid = post(client, environment, user, "create", {"data_source_id": source}).json()["id"]
    task_id = submit(client, environment, user, cid, source, client_request_id="disabled-source").json()["task_id"]
    disabled = json.loads(json.dumps(original))
    disabled["data_sources"][source]["status"] = "disabled"
    await asyncio.to_thread(save_config, environment, disabled)
    engine = create_async_engine(environment["url"], hide_parameters=True)
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        assert await claim(sessions) is None
        failed = (
            await asyncio.to_thread(
                execute,
                environment,
                "SELECT task_status,error_message FROM analysis_tasks WHERE id=$1",
                int(task_id),
            )
        )[0]
        assert failed["task_status"] == "failed" and "数据源未发布或已停用" in failed["error_message"]

        await asyncio.to_thread(save_config, environment, original)
        cid2 = post(client, environment, user, "create", {"data_source_id": source}).json()["id"]
        task_id2 = submit(client, environment, user, cid2, source, client_request_id="disabled-user").json()["task_id"]
        await asyncio.to_thread(execute, environment, "UPDATE users SET status='disabled' WHERE id=$1", int(user["id"]))
        assert await claim(sessions) is None
        failed = (
            await asyncio.to_thread(
                execute,
                environment,
                "SELECT task_status,error_message FROM analysis_tasks WHERE id=$1",
                int(task_id2),
            )
        )[0]
        assert failed["task_status"] == "failed" and "账户或会话已停用" in failed["error_message"]
    finally:
        await asyncio.to_thread(execute, environment, "UPDATE users SET status='active' WHERE id=$1", int(user["id"]))
        await asyncio.to_thread(save_config, environment, original)
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_restart_fails_legacy_running_task(client, environment, model_config):
    from app.worker import serve

    user, _, _ = login(client, environment)
    cid = post(client, environment, user, "create", {"data_source_id": model_config[0]}).json()["id"]
    task_id = submit(client, environment, user, cid, model_config[0], client_request_id="legacy-running").json()[
        "task_id"
    ]
    await asyncio.to_thread(
        execute,
        environment,
        "UPDATE analysis_tasks SET task_status='running',started_at=now(),current_step='旧执行器运行中' WHERE id=$1",
        int(task_id),
    )

    async def unused(_context):
        raise AssertionError("重启恢复不应重新执行旧任务")

    worker = asyncio.create_task(serve(unused))

    async def terminal():
        return await asyncio.to_thread(
            execute,
            environment,
            "SELECT task_status,error_message FROM analysis_tasks WHERE id=$1",
            int(task_id),
        )

    try:
        async with asyncio.timeout(20):
            while (await terminal())[0]["task_status"] != "failed":
                await asyncio.sleep(0.1)
        row = (await terminal())[0]
        assert "执行器重启" in row["error_message"]
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


@pytest.mark.asyncio
async def test_websocket_subscription_broadcast_single_use_and_account_disable(client, environment, model_config):
    import websockets
    from websockets.exceptions import InvalidStatus

    user, _, _ = login(client, environment)
    cid = post(client, environment, user, "create", {}).json()["id"]
    tokens = [
        post(client, environment, user, "ws-token", {"conversation_id": cid}).json()["websocket_token"]
        for _ in range(2)
    ]

    async def connect(token):
        return await websockets.connect(
            environment["app"].replace("https:", "wss:")
            + "/api/chat/ws/chat?conversation_id="
            + cid
            + "&websocket_token="
            + token,
            ssl=environment["tls"],
            origin=environment["app"],
            additional_headers={"Cookie": "ba_session=" + client.cookies.get("ba_session")},
        )

    first, second = await connect(tokens[0]), await connect(tokens[1])
    try:
        assert json.loads(await first.recv())["type"] == "subscription_ready"
        assert json.loads(await second.recv())["type"] == "subscription_ready"
        with pytest.raises(InvalidStatus):
            await connect(tokens[0])
        accepted = submit(client, environment, user, cid, model_config[0]).json()
        values = [json.loads(await asyncio.wait_for(ws.recv(), 5)) for ws in (first, second)]
        assert values[0] == values[1] and values[0]["event_id"] == 1
        assert values[0]["task_id"] == accepted["task_id"]
        await asyncio.to_thread(execute, environment, "UPDATE users SET status='disabled' WHERE id=$1", int(user["id"]))
        await asyncio.wait_for(first.wait_closed(), 10)
        assert first.close_code == 1008
    finally:
        await asyncio.to_thread(execute, environment, "UPDATE users SET status='active' WHERE id=$1", int(user["id"]))
        await first.close()
        await second.close()
        task_post(client, environment, user, accepted["task_id"] + "/cancel")


def test_task_browser_isolated(environment, model_config):
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            "node",
            str(root / "frontend/node_modules/@playwright/test/cli.js"),
            "test",
            "m03.spec.ts",
            "m02.spec.ts",
            "-g",
            "工作台会话|任务发送|模型服务遮掩",
        ],
        cwd=root / "frontend",
        env={**os.environ, "BA_UI_URL": environment["app"], "BA_UI_OUTPUT": str(environment["work"] / "browser")},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    print("Task and model configuration browser checks passed (5 cases).")
