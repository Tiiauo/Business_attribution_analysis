import json
import os
import subprocess
from pathlib import Path

import httpx2
import pytest
from test_auth import execute, login


@pytest.fixture
def sources(environment):
    original = execute(environment, "SELECT config_value FROM system_configs WHERE config_group='runtime'")[0][
        "config_value"
    ]
    ids = execute(
        environment,
        """INSERT INTO data_sources(connection_target,database_name,schema_name)
        VALUES ('{"host":"127.0.0.1","port":15432}','ba_demo','catalog'),
               ('{"host":"127.0.0.1","port":15432}','ba_demo','market') RETURNING id""",
    )
    value = json.loads(original)
    value["data_sources"] = {
        str(row["id"]): {"name": f"fixture-{i}", "credential_ref": "fixture-reference", "status": "enabled"}
        for i, row in enumerate(ids)
    }
    execute(
        environment, "UPDATE system_configs SET config_value=$1::jsonb WHERE config_group='runtime'", json.dumps(value)
    )
    try:
        yield [str(row["id"]) for row in ids]
    finally:
        execute(environment, "UPDATE system_configs SET config_value=$1::jsonb WHERE config_group='runtime'", original)


def post(client, environment, user, path, body):
    return client.post(
        environment["app"] + "/api/chat/" + path,
        json=body,
        headers={"Origin": environment["app"], "X-CSRF-Token": user["csrf_token"]},
    )


def add_round(environment, conversation, user, seq, snapshots=None, state="success"):
    return execute(
        environment,
        """WITH ids AS (SELECT nextval('messages_id_seq') AS mid), task AS (
        INSERT INTO analysis_tasks(conversation_id,user_id,message_id,client_request_id,request_fingerprint,
                                   input_text,task_status,attachment_snapshot_json)
        SELECT $1,$2,mid,$3,repeat('a',64),'fixture',$4,$5::jsonb FROM ids RETURNING id,message_id)
        INSERT INTO messages(id,conversation_id,task_id,role,message_type,content,seq_no)
        SELECT message_id,$1,id,'user','user_input',$3,$6 FROM task RETURNING id,task_id""",
        int(conversation),
        int(user["id"]),
        f"fixture-{seq}",
        state,
        json.dumps(snapshots or []),
        seq,
    )[0]


def test_conversation_lifecycle_sources_and_guards(client, environment, sources):
    user, _, _ = login(client, environment)
    base = environment["app"] + "/api/chat/"
    assert client.post(base + "create", json={}).status_code == 403
    for title, code in (("😀" * 101, "FIELD_TOO_LONG"),):
        response = post(client, environment, user, "create", {"title": title})
        assert response.status_code == 400 and response.json()["code"] == code
    row = post(client, environment, user, "create", {"title": "   "}).json()
    cid = row["conversation_id"]
    assert row["title"] == "新会话" and row["title_source"] == "default"
    assert row["data_source_id"] is None and not row["data_source_locked"]
    for title, code in (("  ", "INVALID_TITLE"), ("😀" * 101, "FIELD_TOO_LONG")):
        response = post(client, environment, user, "update", {"conversation_id": cid, "title": title})
        assert response.status_code == 400 and response.json()["code"] == code
    row = post(
        client, environment, user, "update", {"conversation_id": cid, "title": "😀" * 100, "data_source_id": sources[0]}
    ).json()
    assert row["title_source"] == "manual" and row["data_source_name"] == "fixture-0"
    assert row["last_message_at"] is None
    assert post(client, environment, user, "update", {"conversation_id": cid}).status_code == 400
    assert post(client, environment, user, "create", {"data_source_id": "9223372036854775808"}).status_code == 400
    assert post(client, environment, user, "create", {"data_source_id": "999999"}).status_code == 409
    for actor in ("analyst_b", "admin_demo"):
        with httpx2.Client(verify=environment["tls"], timeout=15) as other:
            identity, _, _ = login(other, environment, actor)
            assert other.get(base + "ls/" + cid).status_code == 404
            assert cid not in [x["id"] for x in other.get(base + "ls").json()["items"]]
            for path in ("update", "archive", "restore"):
                assert (
                    post(
                        other,
                        environment,
                        identity,
                        path,
                        {"conversation_id": cid, **({"title": "x"} if path == "update" else {})},
                    ).status_code
                    == 404
                )
    for action in ("archive", "archive", "restore", "restore"):
        response = post(client, environment, user, action, {"conversation_id": cid})
        assert response.status_code == 200
        assert response.json()["status"] == ("archived" if action == "archive" else "active")
    assert client.get(base + "ls?page_size=0").status_code == 400
    assert client.get(base + "ls?cursor=bad").status_code == 400


def test_history_fixed_boundary_source_lock_and_deleted_snapshots(client, environment, sources):
    from app.contracts import HistoryPage

    user, _, _ = login(client, environment)
    row = post(client, environment, user, "create", {"data_source_id": sources[0]}).json()
    cid = row["conversation_id"]
    snapshot = {
        "attachment_id": "987654",
        "file_name": "已删除的资料.csv",
        "file_type": "csv",
        "file_size": 30,
        "uploaded_at": "2026-10-23T10:00:00+08:00",
        "parse_options": {"has_header": False},
    }
    for seq in range(1, 6):
        add_round(environment, cid, user, seq, [snapshot])
    execute(environment, "UPDATE conversations SET last_message_at='2026-10-23T10:00:00+08:00' WHERE id=$1", int(cid))
    base = environment["app"] + "/api/chat/ls/" + cid
    first = client.get(base + "?page_size=2").json()
    HistoryPage.model_validate(first)
    assert first["snapshot_max_seq_no"] == 5 and [m["seq_no"] for m in first["items"]] == [4, 5]
    ref = first["items"][0]["attachments"][0]
    assert ref["file_name"] == snapshot["file_name"] and ref["parse_options"] == {"has_header": False}
    assert ref["available"] is False and ref["unavailable_reason"] == "deleted" and ref["download_url"] is None
    assert first["conversation"]["data_source_locked"]
    newest = add_round(environment, cid, user, 6)
    second = client.get(base, params={"page_size": 2, "cursor": first["next_cursor"]}).json()
    assert second["snapshot_max_seq_no"] == 5 and [m["seq_no"] for m in second["items"]] == [2, 3]
    third = client.get(base, params={"page_size": 2, "cursor": second["next_cursor"]}).json()
    assert [m["seq_no"] for m in third["items"]] == [1] and third["next_cursor"] is None
    execute(
        environment,
        """INSERT INTO messages(conversation_id,task_id,role,message_type,content,seq_no,
        message_status,tool_name,tool_status,tool_call_id,tool_input_json)
        VALUES($1,$2,'tool','tool_call','已保存的部分输出',7,'failed','text_search','failed','call-1','{"query":"fixture"}')""",
        int(cid),
        newest["task_id"],
    )
    tool = client.get(base).json()["items"][-1]
    assert tool["tool_name"] == "text_search" and tool["tool_status"] == "failed" and tool["tool_call_id"] == "call-1"
    assert tool["tool_input"] == {"query": "fixture"} and tool["tool_outputs"] == []
    assert tool["message_status"] == "failed" and tool["content"] == "已保存的部分输出"
    for cursor in ("bad", f"{int(cid) + 1}|5|4", f"{cid}|5|0", f"{cid}|1|2"):
        assert client.get(base, params={"cursor": cursor}).json()["code"] == "INVALID_CURSOR"
    assert client.get(base + "?page_size=101").json()["code"] == "INVALID_CURSOR"
    rejected = post(
        client,
        environment,
        user,
        "update",
        {"conversation_id": cid, "title": "不能部分更新", "data_source_id": sources[1]},
    )
    assert rejected.status_code == 409 and rejected.json()["code"] == "CONVERSATION_SOURCE_LOCKED"
    execute(
        environment,
        """UPDATE system_configs SET config_value=jsonb_set(
        config_value,ARRAY['data_sources',$1::text,'status'],'"disabled"'::jsonb) WHERE config_group='runtime'""",
        sources[0],
    )
    disabled = client.get(base).json()["conversation"]
    assert disabled["data_source_id"] == sources[0] and disabled["data_source_status"] == "disabled"
    assert post(client, environment, user, "create", {"data_source_id": sources[0]}).status_code == 409
    renamed = post(client, environment, user, "update", {"conversation_id": cid, "title": "人工复核"}).json()
    assert renamed["last_message_at"] == first["conversation"]["last_message_at"]
    assert renamed["title_source"] == "manual" and renamed["data_source_id"] == sources[0]
    for action in ("archive", "restore"):
        result = post(client, environment, user, action, {"conversation_id": cid}).json()
        assert result["data_source_locked"] and result["data_source_id"] == sources[0]
    execute(environment, "UPDATE conversations SET status='deleted',cleanup_status='failed' WHERE id=$1", int(cid))
    tombstone = client.get(base).json()
    assert tombstone["items"] == [] and tombstone["conversation"]["can_retry"]
    assert post(client, environment, user, "restore", {"conversation_id": cid}).status_code == 404


def test_archive_blocking_order_and_list_pagination(client, environment, sources):
    user, _, _ = login(client, environment)
    cid = post(client, environment, user, "create", {}).json()["conversation_id"]
    task = add_round(environment, cid, user, 1, state="queued")
    attachment = execute(
        environment,
        """INSERT INTO attachments(conversation_id,file_name,file_path,file_type,file_size,
        parse_status,delete_status) VALUES($1,'fixture.txt','not-readable','txt',1,'parsing','failed') RETURNING id""",
        int(cid),
    )[0]["id"]
    for code in ("CONVERSATION_BUSY", "ATTACHMENT_DELETE_FAILED", "ATTACHMENT_PARSE_IN_PROGRESS"):
        response = post(client, environment, user, "archive", {"conversation_id": cid})
        assert response.status_code == 409 and response.json()["code"] == code
        if code == "CONVERSATION_BUSY":
            execute(environment, "UPDATE analysis_tasks SET task_status='success' WHERE id=$1", task["task_id"])
        elif code == "ATTACHMENT_DELETE_FAILED":
            execute(environment, "UPDATE attachments SET delete_status='none' WHERE id=$1", attachment)
    execute(environment, "UPDATE attachments SET parse_status='success' WHERE id=$1", attachment)
    assert post(client, environment, user, "archive", {"conversation_id": cid}).status_code == 200
    assert (
        post(client, environment, user, "update", {"conversation_id": cid, "data_source_id": sources[0]}).status_code
        == 409
    )
    ids = {post(client, environment, user, "create", {"title": str(i)}).json()["conversation_id"] for i in range(3)}
    seen, cursor = set(), None
    while True:
        params = {"page_size": 1, **({"cursor": cursor} if cursor else {})}
        page = client.get(environment["app"] + "/api/chat/ls", params=params).json()
        batch = {item["id"] for item in page["items"]}
        assert not seen.intersection(batch)
        seen |= batch
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert ids.issubset(seen)


def test_chat_browser_on_isolated_database(environment, sources):
    root = Path(__file__).resolve().parents[2]
    output = environment["work"] / "browser"
    result = subprocess.run(
        [
            "node",
            str(root / "frontend/node_modules/@playwright/test/cli.js"),
            "test",
            "m03.spec.ts",
            "m01.spec.ts",
            "m02.spec.ts",
            "-g",
            "工作台会话|登录、独立授权|管理员身份|普通用户不能",
        ],
        cwd=root / "frontend",
        env={**os.environ, "BA_UI_URL": environment["app"], "BA_UI_OUTPUT": str(output)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    print("Isolated browser suite passed.")
    print(f"Browser review: {output}")
