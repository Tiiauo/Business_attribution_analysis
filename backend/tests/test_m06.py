import asyncio
import json

import asyncpg
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_auth import execute, login
from test_chat import post

from app.attachments import conversation_path
from app.file_worker import FileWorker


async def test_export_interruption_and_cleanup_retry(client, environment):
    user, _, _ = login(client, environment)
    cid = int(post(client, environment, user, "create", {}).json()["id"])
    task_id = (
        await asyncio.to_thread(
            execute,
            environment,
            """WITH ids AS (SELECT nextval('messages_id_seq') AS mid), task AS (
        INSERT INTO analysis_tasks(conversation_id,user_id,message_id,client_request_id,request_fingerprint,
                                   input_text,task_status,started_at,finished_at)
        SELECT $1,$2,mid,'m06-cleanup',repeat('c',64),'清理测试','success',now(),now()
        FROM ids RETURNING id,message_id)
        INSERT INTO messages(id,conversation_id,task_id,role,message_type,content,seq_no,message_status)
        SELECT message_id,$1,id,'user','user_input','清理测试',1,'complete' FROM task RETURNING task_id""",
            int(cid),
            int(user["id"]),
        )
    )[0]["task_id"]
    export_status = {
        "markdown": {"status": "generating", "error_message": None, "generated_at": None},
        "html": {"status": "not_generated", "error_message": None, "generated_at": None},
    }
    await asyncio.to_thread(
        execute,
        environment,
        """INSERT INTO analysis_results(task_id,conversation_id,problem_definition,key_metrics_json,
        evidence_list_json,conclusion_text,missing_data_text,next_action_text,result_markdown,export_status_json)
        VALUES($1,$2,'清理测试','[]','[]','证据不足','缺少样本','1. 补充数据\n2. 重新分析',
               '## 问题定义\n\n清理测试',$3::jsonb)""",
        int(task_id),
        int(cid),
        json.dumps(export_status),
    )
    export_directory = conversation_path(user["id"], cid, "exports", task_id)
    export_directory.mkdir(parents=True)
    temporary = export_directory / "report.md.tmp"
    temporary.write_text("partial", encoding="utf-8")
    for directory in ("uploads", "workspace"):
        conversation_path(user["id"], cid, directory).mkdir(parents=True)

    engine = create_async_engine(environment["url"], hide_parameters=True)
    ready = await asyncpg.connect(environment["url"].set(drivername="postgresql").render_as_string(hide_password=False))
    await ready.execute("SELECT pg_advisory_lock(743043::bigint)")
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        files = FileWorker(sessions)
        await files.recover()
        state = (
            await asyncio.to_thread(
                execute,
                environment,
                "SELECT export_status_json FROM analysis_results WHERE task_id=$1",
                int(task_id),
            )
        )[0]["export_status_json"]
        state = json.loads(state) if isinstance(state, str) else state
        assert state["markdown"]["status"] == "failed" and "EXPORT_INTERRUPTED" in state["markdown"]["error_message"]
        assert not temporary.exists()

        await asyncio.to_thread(
            execute,
            environment,
            """UPDATE conversations SET status='deleted',cleanup_status='failed',
            cleanup_error='fixture failure',cleanup_updated_at=now() WHERE id=$1""",
            int(cid),
        )
        deleted = post(client, environment, user, "delete", {"conversation_ids": [str(cid)]})
        assert deleted.status_code == 202, deleted.text
        item = deleted.json()["items"][0]
        assert item["cleanup_status"] == "queued" and item["cleanup_error"] is None
        await files.cleanup_conversation(cid, 30, {})
    finally:
        await ready.execute("SELECT pg_advisory_unlock(743043::bigint)")
        await ready.close()
        await engine.dispose()

    assert client.get(environment["app"] + f"/api/chat/ls/{cid}").status_code == 404
    assert not await asyncio.to_thread(execute, environment, "SELECT id FROM conversations WHERE id=$1", int(cid))
    assert not await asyncio.to_thread(execute, environment, "SELECT id FROM analysis_tasks WHERE id=$1", int(task_id))
    for directory in ("uploads", "workspace", "exports"):
        assert not conversation_path(user["id"], cid, directory).exists()
