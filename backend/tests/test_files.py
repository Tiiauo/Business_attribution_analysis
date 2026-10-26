import asyncio
import inspect
import json

import httpx2
import pytest
from test_attachments import workbook
from test_auth import execute, login
from test_chat import post, sources  # noqa: F401
from test_tasks import model_config, submit  # noqa: F401

from app.contracts import AnalysisOutput
from app.core.config import get_settings
from app.worker import serve


async def until(check, timeout=90):
    async with asyncio.timeout(timeout):
        while True:
            value = check()
            if inspect.isawaitable(value):
                value = await value
            if value:
                return value
            await asyncio.sleep(0.1)


async def controlled(context):
    from app.analysis_tools import AnalysisTools
    from app.models import AnalysisTask

    async with context.sessions() as db:
        task = await db.get(AnalysisTask, context.task_id)
    await context.append("已读取测试输入；这是隔离测试执行器。")
    if task.input_text == "wait":
        await asyncio.sleep(120)
    evidence = []
    if task.attachment_snapshot_json:
        tools = AnalysisTools(context, task)
        await tools.prepare_inputs()
        aid = task.attachment_snapshot_json[0]["attachment_id"]
        result = await tools.call("read_file", {"attachment_id": aid}, "read-1")
        evidence = [result["evidence"]]
        stats = await tools.call(
            "run_command",
            {"command": "python_stats", "evidence_id": evidence[0]["evidence_id"], "column": "count"},
            "stats-1",
        )
        assert json.loads(stats["evidence"]["evidence_text"])["sum"] == "30"
        evidence.append(stats["evidence"])
        assert result["preview"][0]["value"]["id"] == "00123"
    return AnalysisOutput(
        problem_definition='<script>alert("x")</script> & 数据',
        key_metrics=[],
        evidence_list=evidence,
        conclusion_text="隔离执行器仅验证链路，不替代真实模型验收。",
        missing_data_text="缺少真实模型结论",
        next_action_text="1. 真实模型联调\n2. 核对附件定位",
    )


@pytest.fixture
async def file_worker(environment, model_config):  # noqa: F811
    await asyncio.to_thread(
        execute,
        environment,
        "UPDATE system_configs SET config_value=jsonb_set(jsonb_set(config_value,'{tools,command_enabled}','true'),'{tools,allowed_commands}','[\"python_stats\"]') WHERE config_group='runtime'",
    )
    worker = asyncio.create_task(serve(controlled))
    try:
        await until(
            lambda: asyncio.to_thread(
                execute,
                environment,
                "SELECT 1 FROM pg_locks WHERE objid=743043 AND granted AND database=(SELECT oid FROM pg_database WHERE datname=current_database())",
            )
        )
        yield model_config
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


def upload(client, environment, user, cid, name="sample.csv", content=b"id,count\n00123,10\n00002,20", **fields):
    return client.post(
        environment["app"] + "/api/attachment/upload",
        data={"conversation_id": cid, **fields},
        files={"file": (name, content)},
        headers={"Origin": environment["app"], "X-CSRF-Token": user["csrf_token"]},
    )


def files(client, environment, cid):
    response = client.get(environment["app"] + "/api/attachment/ls", params={"conversation_id": cid})
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def parsed(client, environment, cid):
    async def check():
        rows = await asyncio.to_thread(files, client, environment, cid)
        return rows if rows and all(r["parse_status"] in {"success", "failed"} for r in rows) else None

    return await until(check)


def api_post(client, environment, user, path, body):
    return client.post(
        environment["app"] + "/api/" + path,
        json=body,
        headers={"Origin": environment["app"], "X-CSRF-Token": user["csrf_token"]},
    )


@pytest.mark.asyncio
async def test_attachment_boundary_parse_options_download_and_isolation(client, environment, file_worker):
    user, _, _ = login(client, environment)
    cid = post(client, environment, user, "create", {}).json()["id"]
    assert upload(client, environment, user, cid, has_header="1").json()["code"] == "INVALID_TABLE_OPTIONS"
    assert upload(client, environment, user, cid, name="text.txt", has_header="false").status_code == 400
    assert upload(client, environment, user, cid, name="../escape.csv").status_code == 400
    assert upload(client, environment, user, cid, name="macro.xlsm").status_code == 400
    first = upload(client, environment, user, cid, name="SAMPLE.CSV", has_header="false")
    assert first.status_code == 202, first.text
    assert first.json()["parse_options"] == {"has_header": False}
    text = upload(client, environment, user, cid, name="note.markdown", content="资料😀".encode())
    assert text.status_code == 202 and text.json()["parse_options"] == {}
    rows = await parsed(client, environment, cid)
    assert all(row["parse_status"] == "success" for row in rows), [(r["id"], r["parse_error"]) for r in rows]
    csv = next(row for row in rows if row["file_type"] == "csv")
    assert csv["parse_summary"][0]["row_count"] == 3
    assert csv["parse_summary"][0]["columns"] == ["column_1", "column_2"]
    assert client.get(environment["app"] + csv["download_url"]).content.startswith(b"id,count")
    with httpx2.Client(verify=environment["tls"]) as other:
        login(other, environment, username="analyst_b")
        assert other.get(environment["app"] + csv["download_url"]).status_code == 404
        assert other.get(environment["app"] + "/api/attachment/ls", params={"conversation_id": cid}).status_code == 404
    assert post(client, environment, user, "archive", {"conversation_id": cid}).status_code == 200
    assert upload(client, environment, user, cid).json()["code"] == "CONVERSATION_ARCHIVED"
    assert api_post(client, environment, user, "attachment/delete", {"attachment_id": csv["id"]}).status_code == 409
    assert client.get(environment["app"] + csv["download_url"]).status_code == 200


@pytest.mark.asyncio
async def test_attachment_task_real_inputs_exports_and_cleanup(client, environment, file_worker):
    user, _, _ = login(client, environment)
    cid = post(client, environment, user, "create", {"data_source_id": file_worker[0]}).json()["id"]
    first = upload(client, environment, user, cid).json()
    second = upload(client, environment, user, cid, content=b"id,count\n99999,88").json()
    rows = await parsed(client, environment, cid)
    assert all(row["parse_status"] == "success" for row in rows), [(r["id"], r["parse_error"]) for r in rows]
    accepted = submit(client, environment, user, cid, file_worker[0], attachment_ids=[first["id"]]).json()
    tid = accepted["task_id"]
    await until(
        lambda: asyncio.to_thread(
            execute,
            environment,
            "SELECT id FROM analysis_tasks WHERE id=$1 AND task_status IN ('success','failed')",
            int(tid),
        )
    )
    result = client.get(environment["app"] + f"/api/results/{tid}")
    assert result.status_code == 200, result.text
    result = result.json()
    root = get_settings().data_root
    task_input = root / "workspace" / user["id"] / cid / "tasks" / tid / "input"
    manifest = json.loads((task_input / "manifest.json").read_text(encoding="utf-8"))
    assert {item["attachment_id"] for item in manifest} == {first["id"]}
    assert manifest[0]["parse_options"] == {"has_header": True}
    assert not (task_input / second["id"]).exists()
    assert result["evidence_list"][0]["source_locator"]["position"]["items"][0]["line_start"] == 2
    assert result["export_status"]["markdown"]["status"] == "not_generated"
    assert not (root / "exports" / user["id"] / cid).exists()
    url = f"results/{tid}/export"
    assert client.get(environment["app"] + f"/api/results/{tid}/download").json()["code"] == "EXPORT_NOT_GENERATED"
    response = api_post(client, environment, user, url, {"format": "markdown"})
    assert response.status_code == 202
    assert api_post(client, environment, user, url, {"format": "markdown"}).status_code == 202
    await until(
        lambda: asyncio.to_thread(
            execute,
            environment,
            "SELECT id FROM analysis_results WHERE task_id=$1 AND export_status_json->'markdown'->>'status'='success'",
            int(tid),
        )
    )
    markdown = client.get(environment["app"] + f"/api/results/{tid}/download?format=markdown").content
    assert markdown.decode() == result["result_markdown"]
    assert api_post(client, environment, user, url, {"format": "markdown"}).status_code == 200
    assert api_post(client, environment, user, url, {"format": "html"}).status_code == 202
    await until(
        lambda: asyncio.to_thread(
            execute,
            environment,
            "SELECT id FROM analysis_results WHERE task_id=$1 AND export_status_json->'html'->>'status'='success'",
            int(tid),
        )
    )
    document = client.get(environment["app"] + f"/api/results/{tid}/download?format=html").text
    assert "<script>" not in document and "&lt;script&gt;" in document and "default-src" in document
    assert "问题定义" in document and "下一步建议" in document
    html_path = root / "exports" / user["id"] / cid / tid / "report.html"
    html_path.unlink()
    assert client.get(environment["app"] + f"/api/results/{tid}/download?format=html").json()["code"] == "EXPORT_FAILED"
    assert api_post(client, environment, user, url, {"format": "html"}).status_code == 202
    await until(
        lambda: asyncio.to_thread(
            execute,
            environment,
            "SELECT id FROM analysis_results WHERE task_id=$1 AND export_status_json->'html'->>'status'='success'",
            int(tid),
        )
    )
    deleted = api_post(client, environment, user, "attachment/delete", {"attachment_id": first["id"]})
    assert deleted.status_code == 202, deleted.text
    assert (
        client.get(environment["app"] + f"/api/attachment/get?attachment_id={first['id']}").json()["code"]
        == "ATTACHMENT_DELETE_IN_PROGRESS"
    )
    await until(lambda: not any(row["id"] == first["id"] for row in files(client, environment, cid)))
    assert not (task_input / first["id"]).exists()
    after = client.get(environment["app"] + f"/api/results/{tid}").json()
    locator = after["evidence_list"][0]["source_locator"]
    assert locator["available"] is False and locator["available_at_result"] is True
    assert after["result_markdown"] == result["result_markdown"]
    assert client.get(environment["app"] + f"/api/results/{tid}/download?format=markdown").content == markdown
    stored_outputs = await asyncio.to_thread(
        execute,
        environment,
        "SELECT tool_outputs_json FROM messages WHERE conversation_id=$1 AND role='tool'",
        int(cid),
    )
    assert all(
        bool(ref.get("attachment_id"))
        if ref["type"] == "attachment"
        else bool(ref.get("task_id") and ref.get(ref["type"] + "_id"))
        for row in stored_outputs
        for output in (
            json.loads(row["tool_outputs_json"])
            if isinstance(row["tool_outputs_json"], str)
            else row["tool_outputs_json"]
        )
        for ref in output["input_refs"]
    ), stored_outputs
    history = client.get(environment["app"] + "/api/chat/ls/" + cid).json()
    outputs = [o for m in history["items"] for o in m["tool_outputs"]]
    assert outputs
    assert all(
        not o["available"] and o["unavailable_reason"] == "attachment_deleted"
        for o in outputs
        if o["retention_kind"] == "input_copy"
    )
    assert all(
        o["available"] and all(not ref["available"] for ref in o["input_refs"])
        for o in outputs
        if o["retention_kind"] == "derived"
    )
    assert len(list((task_input.parent / "output").iterdir())) == 1
    cleanup = post(client, environment, user, "delete", {"conversation_ids": [cid]})
    assert cleanup.status_code == 202, cleanup.text
    assert client.get(environment["app"] + f"/api/results/{tid}").status_code == 404
    await until(lambda: client.get(environment["app"] + "/api/chat/ls/" + cid).status_code == 404)
    for directory in ("uploads", "workspace", "exports"):
        assert not (root / directory / user["id"] / cid).exists()
    assert not await asyncio.to_thread(
        execute, environment, "SELECT id FROM analysis_tasks WHERE conversation_id=$1", int(cid)
    )


@pytest.mark.asyncio
async def test_formula_failure_task_busy_and_batch_authorization(client, environment, file_worker):
    user, _, _ = login(client, environment)
    cid = post(client, environment, user, "create", {"data_source_id": file_worker[0]}).json()["id"]
    content = workbook('<row r="7"><c r="A7"><f t="shared" si="1"/><v>1</v></c></row>', hidden=True)
    item = upload(client, environment, user, cid, name="unsafe.xlsx", content=content).json()
    rows = await parsed(client, environment, cid)
    assert rows[0]["parse_status"] == "failed" and "XLSX_FORMULA_UNSUPPORTED" in rows[0]["parse_error"]
    assert "A7" in rows[0]["parse_error"]
    response = submit(client, environment, user, cid, file_worker[0], attachment_ids=[item["id"]])
    assert response.json()["code"] == "ATTACHMENT_NOT_READY"
    task = submit(client, environment, user, cid, file_worker[0], client_request_id="waiting", content="wait").json()
    await until(
        lambda: asyncio.to_thread(
            execute,
            environment,
            "SELECT id FROM analysis_tasks WHERE id=$1 AND task_status='running'",
            int(task["task_id"]),
        )
    )
    assert upload(client, environment, user, cid).json()["code"] == "CONVERSATION_BUSY"
    assert (
        api_post(client, environment, user, "attachment/delete", {"attachment_id": item["id"]}).json()["code"]
        == "CONVERSATION_BUSY"
    )
    assert (
        post(client, environment, user, "delete", {"conversation_ids": [cid, "9223372036854775807"]}).status_code == 404
    )
    assert client.get(environment["app"] + "/api/chat/ls/" + cid).json()["conversation"]["status"] == "active"
    assert post(client, environment, user, "delete", {"conversation_ids": [cid]}).status_code == 202
    await until(lambda: client.get(environment["app"] + "/api/chat/ls/" + cid).status_code == 404)
