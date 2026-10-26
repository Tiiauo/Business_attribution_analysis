import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_auth import execute, login
from test_chat import post
from test_chat import sources as sources_fixture  # noqa: F401

from app.analysis import context_messages, estimate_tokens, validate_output, validate_submission
from app.analysis_tools import readonly_query
from app.core.errors import ApiError
from app.sandbox import container_config, docker, namespace, run_command, run_script
from app.worker import TaskContext


@pytest.mark.parametrize(
    ("invalid_body_characters", "invalid_attempts", "exploration_rounds"), [(0, 1, 1), (32000, 2, 1), (0, 1, 12)]
)
async def test_streamed_tool_fragments_accept_protocol_nulls(
    monkeypatch, invalid_body_characters, invalid_attempts, exploration_rounds
):
    import app.analysis as analysis

    task = SimpleNamespace(
        task_kind="final_report",
        conversation_id=1,
        config_snapshot_json={
            "model": {"credential_ref": "fixture"},
            "context": {"response_reserved_tokens": 16384, "context_window_tokens": 40000},
        },
    )

    @asynccontextmanager
    async def sessions():
        yield SimpleNamespace(get=AsyncMock(return_value=task))

    tools = SimpleNamespace(
        definitions=lambda: [
            {"type": "function", "function": {"name": "inspect_database", "parameters": {"type": "object"}}}
        ],
        evidence={},
        prepare_inputs=AsyncMock(),
        call=AsyncMock(return_value={"source": "original-tool-evidence"}),
    )
    monkeypatch.setattr(analysis, "AnalysisTools", lambda *_: tools)
    monkeypatch.setattr(analysis, "resolve_credential", AsyncMock(return_value="synthetic"))
    monkeypatch.setattr(analysis, "context_messages", AsyncMock(return_value=[]))
    body = {
        "problem_definition": "protocol fixture",
        "key_metrics": [],
        "evidence_list": [],
        "conclusion_text": "证据不足",
        "missing_data_text": "缺数据",
        "next_action_text": "1. 补充数据\n2. 核对口径",
    }

    stream_calls = []

    async def stream(*args, **kwargs):
        stream_calls.append(json.loads(json.dumps(args[2])))
        if len(stream_calls) > 12:
            assert [item["function"]["name"] for item in kwargs["tools"]] == ["submit_analysis"]
        if len(stream_calls) <= exploration_rounds:
            yield {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": f"inspect-{len(stream_calls)}",
                                    "function": {"name": "inspect_database", "arguments": ""},
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
            return
        yield {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": f"submit-{len(stream_calls)}",
                                "function": {"name": "submit_analysis", "arguments": ""},
                            }
                        ]
                    }
                }
            ]
        }
        yield {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": None,
                                "function": {
                                    "name": None,
                                    "arguments": json.dumps(
                                        {
                                            **body,
                                            "problem_definition": "x" * invalid_body_characters
                                            or body["problem_definition"],
                                            "next_action_text": "only one action",
                                        }
                                        if exploration_rounds
                                        < len(stream_calls)
                                        <= exploration_rounds + invalid_attempts
                                        else body
                                    ),
                                },
                            }
                        ]
                    }
                }
            ]
        }
        yield {"choices": [{"delta": None, "finish_reason": "tool_calls"}]}

    monkeypatch.setattr(analysis, "stream_chat", stream)
    context = SimpleNamespace(sessions=sessions, task_id=1, append=AsyncMock())
    result = await analysis.run_analysis(context)
    assert result.problem_definition == "protocol fixture"
    assert tools.call.await_count == exploration_rounds
    tools.call.assert_any_await("inspect_database", {}, "inspect-1")
    assert len(stream_calls) == exploration_rounds + 1 + invalid_attempts
    assert context.append.await_count == invalid_attempts
    retry = stream_calls[-1]
    assert any("original-tool-evidence" in str(message) for message in retry)
    assert retry[-2]["tool_calls"][0]["id"] == f"submit-{invalid_attempts + exploration_rounds}"
    assert retry[-2]["tool_calls"][0]["function"]["arguments"] == "{}"
    assert retry[-1]["role"] == "tool"
    assert retry[-1]["tool_call_id"] == f"submit-{invalid_attempts + exploration_rounds}"
    assert "ANALYSIS_STRUCTURE_INVALID" in retry[-1]["content"]


@pytest.mark.parametrize(
    "query",
    [
        "DELETE FROM market.orders",
        "SELECT * FROM catalog.products",
        "SELECT * FROM orders",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT pg_sleep(1)",
        "SELECT count(*) FROM pg_catalog.pg_class",
        "SELECT * INTO market.copy FROM market.orders",
        "SELECT * FROM market.orders FOR UPDATE",
        "SELECT 1; SELECT 2",
        "WITH q AS (DELETE FROM market.orders RETURNING *) SELECT * FROM q",
        "SELECT public.escape()",
    ],
)
def test_sql_rejects_out_of_scope_and_effects(query):
    with pytest.raises(
        ApiError,
    ) as caught:
        readonly_query(query, "market")
    assert caught.value.code == "SQL_NOT_ALLOWED"


def test_query_budget_and_evidence_integrity():
    from app.core.config import ROOT

    for schema in ("behavior", "market"):
        assert readonly_query((ROOT / "samples/queries" / (schema + ".sql")).read_text(encoding="utf-8"), schema)
    for query in (
        "SELECT count(*) FROM market.orders",
        "WITH q AS (SELECT * FROM market.orders) SELECT count(*) FROM q",
        "SELECT sum(CASE WHEN status='paid' THEN 1 ELSE 0 END) FROM market.orders",
        "SELECT date_trunc('month',paid_at),count(*) FROM market.orders GROUP BY 1",
        "SELECT sum(CASE WHEN status='paid' AND paid_amount>0 THEN 1 ELSE 0 END) FROM market.orders",
        "SELECT count(*) FROM market.orders WHERE status='paid' OR status='completed'",
    ):
        assert readonly_query(query, "market")
    assert estimate_tokens([{"role": "user", "content": "中😀"}], []) == 4199
    base = {
        "problem_definition": "测试",
        "key_metrics": [],
        "evidence_list": [],
        "conclusion_text": "证据不足",
        "missing_data_text": "缺少口径",
        "next_action_text": "1. 明确范围\n2. 补充样本",
    }
    assert validate_output(base, {}).evidence_list == []
    with pytest.raises(ApiError) as invalid_metric:
        validate_output(
            {
                **base,
                "key_metrics": [
                    {
                        "metric_name": "人数",
                        "metric_value": "120→60",
                        "metric_unit": "人",
                        "metric_period": "两周",
                    }
                ],
            },
            {},
        )
    assert '"field":["key_metrics",0,"metric_value","int"]' in invalid_metric.value.message
    assert "int_type" in invalid_metric.value.message
    assert "120→60" not in invalid_metric.value.message
    for text in (
        "1. 明确范围 2. 补充样本",
        "一、明确范围 二、补充样本",
        "建议一：明确范围 建议二：补充样本",
        "明确范围；补充样本",
    ):
        assert validate_output({**base, "next_action_text": text}, {})
    evidence = {
        "evidence_id": "E0001",
        "source_type": "database",
        "source_name": "样例源",
        "evidence_text": '[{"n":60}]',
        "related_metric": "记录数，实际COUNT结果可复算",
        "confidence": "high",
        "source_locator": {
            "data_source_id": "1",
            "database": "demo",
            "schema": "market",
            "query_text": "SELECT count(*) AS n FROM market.orders",
            "filter_summary": "全部记录",
            "extracted_at": "2026-10-23T08:00:00Z",
        },
    }
    from app.contracts import Evidence

    evidence = Evidence.model_validate(evidence).model_dump(mode="json", by_alias=True)
    base["evidence_list"] = [evidence]
    assert validate_output(base, {"E0001": evidence})
    altered = json.loads(json.dumps(base))
    altered["evidence_list"][0]["evidence_text"] = '[{"n":600}]'
    with pytest.raises(ApiError):
        validate_output(altered, {"E0001": evidence})
    reference = {"evidence_id": "E0001", "related_metric": "核对记录总数", "confidence": "medium"}
    submitted = {**base, "evidence_list": [reference]}
    resolved = validate_submission(submitted, {"E0001": evidence}).evidence_list[0]
    assert resolved.evidence_text == evidence["evidence_text"]
    assert resolved.source_locator.model_dump(mode="json") == evidence["source_locator"]
    assert resolved.related_metric == "核对记录总数" and resolved.confidence == "medium"
    assert submitted["evidence_list"] == [reference]
    for invalid in (
        [{**reference, "evidence_id": "invented"}],
        [{**reference, "evidence_text": "forged"}],
        [{**reference, "source_locator": evidence["source_locator"]}],
        [reference, reference],
    ):
        with pytest.raises(ApiError):
            validate_submission({**base, "evidence_list": invalid}, {"E0001": evidence})
    with pytest.raises(ApiError):
        validate_submission(submitted, {"E0001": {**evidence, "confidence": "low"}})
    base["evidence_list"] = [{"evidence_id": "invented"}]
    with pytest.raises(ApiError):
        validate_output(base, {})


@pytest.mark.asyncio
async def test_context_summary_persists_and_keeps_recent_history(
    client,
    environment,
    sources_fixture,  # noqa: F811
    monkeypatch,
):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import AnalysisTask

    user, _, _ = login(client, environment)
    cid = post(client, environment, user, "create", {"data_source_id": sources_fixture[0]}).json()["id"]
    prior = (
        await asyncio.to_thread(
            execute,
            environment,
            """WITH ids AS (SELECT nextval('messages_id_seq') AS mid), task AS (
        INSERT INTO analysis_tasks(conversation_id,user_id,message_id,client_request_id,request_fingerprint,
                                   input_text,task_status)
        SELECT $1,$2,mid,'history',repeat('a',64),'历史问题','success' FROM ids RETURNING id,message_id)
        INSERT INTO messages(id,conversation_id,task_id,role,message_type,content,seq_no,message_status)
        SELECT message_id,$1,id,'user','user_input','历史问题',1,'complete' FROM task RETURNING task_id""",
            int(cid),
            int(user["id"]),
        )
    )[0]["task_id"]
    await asyncio.to_thread(
        execute,
        environment,
        """INSERT INTO messages(conversation_id,task_id,role,message_type,content,seq_no,message_status)
        SELECT $1,$2,'assistant','assistant_text',repeat('历史分析',80),n,'complete'
        FROM generate_series(2,11) AS n""",
        int(cid),
        int(prior),
    )
    context = {
        "model": {
            "api_url": "https://model.invalid/v1",
            "model_id": "fixture",
            "credential_ref": "oidc:bootstrap",
            "timeout_seconds": 1,
        },
        "context": {
            "context_window_tokens": 20000,
            "response_reserved_tokens": 512,
            "summary_trigger_ratio": 0.1,
            "summary_max_tokens": 256,
            "recent_message_count": 2,
        },
        "tools": {},
        "data_sources": {},
    }
    current = (
        await asyncio.to_thread(
            execute,
            environment,
            """WITH ids AS (SELECT nextval('messages_id_seq') AS mid), task AS (
        INSERT INTO analysis_tasks(conversation_id,user_id,message_id,client_request_id,request_fingerprint,
                                   input_text,task_status,config_snapshot_json)
        SELECT $1,$2,mid,'current',repeat('b',64),'当前问题','running',$3::jsonb FROM ids
        RETURNING id,message_id)
        INSERT INTO messages(id,conversation_id,task_id,role,message_type,content,seq_no,message_status)
        SELECT message_id,$1,id,'user','user_input','当前问题',12,'complete' FROM task RETURNING task_id""",
            int(cid),
            int(user["id"]),
            json.dumps(context),
        )
    )[0]["task_id"]

    calls = []

    async def fake_chat_once(config, secret, messages, **kwargs):
        calls.append((config["model_id"], secret, messages, kwargs))
        return {"choices": [{"message": {"content": "历史摘要正文"}}]}

    monkeypatch.setattr("app.analysis.chat_once", fake_chat_once)
    engine = create_async_engine(environment["url"], hide_parameters=True)
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            task = await db.get(AnalysisTask, int(current))

        class Tools:
            source_id = sources_fixture[0]
            source = {"name": "fixture", "database_name": "ba_demo", "schema_name": "market"}
            evidence = {}
            manifest = []

            async def historical(self):
                return []

        messages = await context_messages(TaskContext(sessions, int(current)), task, Tools(), [], "synthetic")
    finally:
        await engine.dispose()
    assert calls and calls[0][0] == "fixture"
    assert any("历史摘要正文" in item["content"] for item in messages)
    assert sum(item["content"] == "当前问题" for item in messages) == 1
    rows = await asyncio.to_thread(
        execute,
        environment,
        "SELECT start_seq_no,end_seq_no,summary_text FROM context_summaries WHERE conversation_id=$1",
        int(cid),
    )
    assert [dict(row) for row in rows] == [{"start_seq_no": 1, "end_seq_no": 9, "summary_text": "历史摘要正文"}]


@pytest.mark.asyncio
async def test_real_sandbox_stats_isolation_and_cancel():
    result = json.loads(await run_command("python_stats", {"values": ["10", "20", "30"]}, "sandbox-test", 10))
    assert result["sum"] == "60" and result["mean"] == "20"
    probe = """import os,socket,json
assert os.getuid()==10001
assert not os.path.exists('/run/secrets/master_key')
try:
 open('/unauthorized','w').write('x')
 raise AssertionError('Root filesystem writable')
except OSError: pass
s=socket.socket();s.settimeout(1)
try:
 s.connect(('1.1.1.1',443))
 raise AssertionError('Network allowed')
except OSError: pass
print(json.dumps({'isolated':True}))
"""
    assert json.loads(await run_script(probe, {}, "sandbox-test", 10))["isolated"]
    host = container_config("fixed-image", {}, "fixture-input", run=True)["HostConfig"]
    assert host["Memory"] == 268435456 and host["PidsLimit"] == 32 and host["Mounts"][0]["ReadOnly"]
    running = asyncio.create_task(run_script("import time\ntime.sleep(120)\n", {}, "sandbox-cancel", 120))
    await asyncio.sleep(1)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    async with docker() as client:
        filters = json.dumps({"label": ["ba.worker=" + namespace(), "ba.task=sandbox-cancel"]})
        assert (await client.get("/containers/json", params={"all": "true", "filters": filters})).json() == []
        assert not (await client.get("/volumes", params={"filters": filters})).json().get("Volumes")
