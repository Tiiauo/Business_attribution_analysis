"""Run the two documented real-model demos against the primary Compose delivery."""

import asyncio
import hashlib
import json
import os
import re
import ssl
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.database import Session  # noqa: E402
from app.runtime import resolve_credential, runtime_row  # noqa: E402

REMOTE = os.environ.get("BA_COMPOSE_REMOTE", "/home/secops/business-attribution/compose")
if not re.fullmatch(r"/home/secops/business-attribution/[a-z][a-z0-9-]*", REMOTE):
    raise ValueError("Invalid delivery project directory")
PACKAGE = Path(os.environ.get("BA_COMPOSE_PACKAGE", ROOT / ".run/compose-package"))
APP = os.environ.get("BA_UI_URL", "https://192.168.10.22")
IDP = os.environ.get("BA_UI_ISSUER", "https://192.168.10.22:9443")


def runtime_digest():
    digest = hashlib.sha256()
    for path in sorted((ROOT / "backend/app").rglob("*.py")):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def remote(command, *, data=None):
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "secops@192.168.10.22", command],
        input=data,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    if result.returncode:
        raise RuntimeError("Acceptance Compose operation failed; inspect only this project's services")
    return result.stdout


def login(client, username="analyst_demo"):
    account = next(
        row
        for row in json.loads((PACKAGE / ".secrets/demo-accounts.json").read_text(encoding="utf-8"))
        if row["username"] == username
    )
    response = client.get(APP + "/auth/login")
    form = client.get(response.headers["location"])
    csrf = re.search(r'name="csrf" value="([^"]+)"', form.text).group(1)
    response = client.post(
        IDP + "/authorize",
        data={"csrf": csrf, **{key: account[key] for key in ("username", "password", "role")}},
        headers={"Origin": IDP},
    )
    callback = client.get(response.headers["location"])
    assert callback.status_code == 303 and "error=" not in callback.headers["location"]
    response = client.get(APP + "/auth/me")
    assert response.status_code == 200
    return response.json()


def post(client, user, path, body):
    response = client.post(APP + path, json=body, headers={"Origin": APP, "X-CSRF-Token": user["csrf_token"]})
    if response.status_code not in {200, 202}:
        raise RuntimeError(f"Acceptance request failed: {response.status_code} {response.json().get('code')}")
    return response.json()


def wait_task(client, tid):
    deadline = time.monotonic() + 620
    while time.monotonic() < deadline:
        value = client.get(APP + "/api/tasks/" + tid).json()
        if value["task_status"] not in {"queued", "running"}:
            if value["task_status"] != "success":
                raise RuntimeError(f"Real task {tid}: {value['task_status']}: {value['error_message']}")
            return value
        time.sleep(0.5)
    raise TimeoutError("Real-model task did not finish")


def usage(tids):
    sql = "SELECT coalesce(json_agg(log_content::json),'[]') FROM task_logs WHERE log_type='model_usage'"
    records = json.loads(
        remote(f'cd {REMOTE} && docker compose exec -T db psql -U ba_app -d ba_compose -At -c "{sql}"')
    )
    prompt = sum(row.get("prompt_tokens") or 0 for row in records)
    completion = sum(row.get("completion_tokens") or 0 for row in records)
    # Peak prices, no cache discount, and a conservative 10 CNY/USD conversion bound.
    upper = (prompt * 0.30 + completion * 1.20) / 1_000_000 * 10
    return {
        "calls": len(records),
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cny_upper_estimate": upper,
    }


async def configured_model():
    async with Session() as db:
        value = (await runtime_row(db)).config_value
        return value["model"], value["context"], await resolve_credential(db, value["model"]["credential_ref"])


def prepare():
    remote(f"cd {REMOTE} && docker compose run --rm migrate -m app.sample_data")
    model, context, secret = asyncio.run(configured_model())
    context = {**context, "response_reserved_tokens": 16384}
    if model["model_id"] != "deepseek-v4.1-flash" or model["api_url"] != "https://opencode.ai/zen/go/v1":
        raise RuntimeError("Recheck the provider price before running a different configured model")
    with httpx2.Client(verify=ssl.create_default_context(cafile=str(PACKAGE / "ca.crt")), timeout=30) as client:
        user = login(client, "admin_demo")
        state = client.get(APP + "/api/admin/configs").json()
        state = post(
            client,
            user,
            "/api/admin/configs",
            {
                "expected_version": state["edit_version"],
                "changes": {
                    "model": {key: model[key] for key in ("api_url", "model_id", "timeout_seconds")},
                    "context": context,
                },
                "credentials": {"model": secret},
            },
        )
        post(client, user, "/api/admin/reload", {"expected_version": state["edit_version"]})
        sources = client.get(APP + "/api/data-sources?view=admin").json()["items"]
        for source in sources:
            value = post(client, user, f"/api/admin/data-sources/{source['id']}/test", {})
            assert value["status"] == "success", source["id"]
    print(
        "Independent delivery: six read-only sources published; model configured without printing or copying secrets.",
        flush=True,
    )


DEMOS = {
    "behavior": {
        "title": "客户行为分析 · 电脑端演示",
        "file": "client_error_summary.csv",
        "questions": [
            "为什么本期客户下单转化下降？比较两周客户访问、加购、有效下单的严格漏斗，请列明统计口径和证据。每周独立筛选事件后，再计算该周每用户的首次访问；同一用户可分别进入两周漏斗，不能用两周全局首次访问时间归期。同一期同用户先访问、后加购、再paid/completed下单，按该周首次访问设备互斥归属。",
            "仅按Windows与macOS电脑拆解，哪个环节变化最大？请沿用两期口径并核对分组与整体。",
            "结合新增资料更新判断，区分已证实事实与待验证解释，说明缺失数据，并给出至少两项可执行排查建议及验证方式。",
        ],
    },
    "market": {
        "title": "市场表现分析 · 电脑端演示",
        "file": "promotion_calendar.md",
        "questions": [
            "为什么本期销售收入下降，投放效率是否同步恶化？请比较两周有效支付收入、订单及投放，核对明细与汇总。仅paid/completed按paid_at归期，订单粒度去重，投放独立聚合后关联，缺支付时间单列缺口。",
            "分渠道拆解收入变化，再比较 ROAS 和获单成本，核对渠道之和与整体。",
            "结合新增资料更新判断。请区分事实、可能解释和缺失数据，不把时间重合写成因果；提出至少两项渠道排查或小规模验证建议，注明衡量指标、证据和局限。",
        ],
    },
}


def demos(output):
    output.mkdir(parents=True, exist_ok=True)
    ledger, tids = {}, []
    prior_cny = float(os.environ.get("BA_PRIOR_MODEL_CNY", "0"))
    assert 0 <= prior_cny <= 66
    tls = ssl.create_default_context(cafile=str(PACKAGE / "ca.crt"))
    with httpx2.Client(verify=tls, timeout=30) as client:
        user = login(client)
        sources = client.get(APP + "/api/data-sources").json()["items"]
        model, context, _ = asyncio.run(configured_model())
        context = {**context, "response_reserved_tokens": 16384}
        reserve = (
            16 * (context["context_window_tokens"] * 0.30 + context["response_reserved_tokens"] * 1.20) / 1_000_000 * 10
        )
        for schema, demo in DEMOS.items():
            source = next(row for row in sources if row["schema"] == schema)
            rounds = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in (output / f"{schema}-round-{index}.json" for index in range(1, 5))
                if path.exists()
            ]
            if rounds:
                cid = rounds[0]["snapshot"]["conversation_id"]
                for saved in rounds:
                    current = client.get(APP + "/api/results/" + saved["task_id"]).json()
                    assert current["result_markdown"] == saved["result"]["result_markdown"]
            else:
                cid = post(client, user, "/api/chat/create", {"title": demo["title"], "data_source_id": source["id"]})[
                    "id"
                ]
            for index in range(4):
                if index < len(rounds):
                    continue
                current_usage = usage(tids)
                if prior_cny + current_usage["cny_upper_estimate"] + reserve > 66:
                    raise RuntimeError("Budget reserve exceeds the authorized 66 CNY; no new task submitted")
                attachments = []
                if index == 2:
                    path = ROOT / "samples/demos" / demo["file"]
                    response = client.post(
                        APP + "/api/attachment/upload",
                        data={"conversation_id": cid},
                        files={"file": (path.name, path.read_bytes())},
                        headers={"Origin": APP, "X-CSRF-Token": user["csrf_token"]},
                    )
                    assert response.status_code == 202, response.status_code
                    aid = response.json()["id"]
                    deadline = time.monotonic() + 90
                    while time.monotonic() < deadline:
                        attachment = client.get(APP + "/api/attachment/ls", params={"conversation_id": cid}).json()[
                            "items"
                        ][0]
                        if attachment["parse_status"] in {"success", "failed"}:
                            assert attachment["parse_status"] == "success", attachment["parse_error"]
                            break
                        time.sleep(0.5)
                    else:
                        raise TimeoutError("Demo attachment parsing")
                    attachments = [aid]
                question = (
                    demo["questions"][index]
                    if index < 3
                    else "请汇总此前三轮成功分析生成最终报告，完整覆盖总体变化、分组拆解、新增资料和结论变化、未验证事项。至少保留一条数据库证据和一条新增附件证据，原样复制可引用的历史证据。不要重新取数，不将相关性当作因果证明。复核历史查询的关联粒度：查询返回重复行不能证明源表重复，若CTE保留两期而输出只按渠道展示，应明确是查询关联或展示粒度问题；未直接验证的源表质量问题只能列为待核实，不能写成已证实事实。"
                )
                question = (
                    "基期2026-09-01至09-07，分析期2026-09-08至09-14，时区Asia/Shanghai。"
                    + question
                    + " 仅分析本轮所问指标，不扩展客户画像、分群或其他场景。优先一次聚合查询。六部分简洁提交，指标不超过8项，证据最多2条，不重复提交全部探索证据。"
                    + " 引用证据时逐字复制source_locator的全部原字段及原值（包括空值、原始query_text、filter_summary和extracted_at），不重新格式化SQL、不推测或替换提取时间。"
                )
                accepted = post(
                    client,
                    user,
                    "/api/chat/send",
                    {
                        "conversation_id": cid,
                        "data_source_id": source["id"],
                        "client_request_id": f"m07-{schema}-{index}-{uuid4().hex[:8]}",
                        "task_kind": "analysis" if index < 3 else "final_report",
                        "content": question,
                        "attachment_ids": attachments,
                    },
                )
                tid = accepted["task_id"]
                tids.append(tid)
                print(f"{schema} round {index + 1}: task {tid} accepted", flush=True)
                snapshot = wait_task(client, tid)
                result = client.get(APP + "/api/results/" + tid).json()
                assert all(
                    result[key]
                    for key in ("problem_definition", "conclusion_text", "missing_data_text", "next_action_text")
                )
                assert result["key_metrics"] and result["evidence_list"]
                if index == 2:
                    assert any(e["source_type"] == "file" for e in result["evidence_list"]), (
                        "New attachment was not used"
                    )
                if index == 3:
                    assert result["final_report_scope"]["source_task_ids"] == [r["task_id"] for r in rounds]
                    assert not any(
                        m["tool_name"] in {"query_database", "read_file", "search_text", "inspect_database"}
                        for m in snapshot["messages"]
                    )
                    assert any(e["source_type"] == "file" for e in result["evidence_list"])
                rounds.append({"task_id": tid, "result": result, "snapshot": snapshot})
                (output / f"{schema}-round-{index + 1}.json").write_text(
                    json.dumps(rounds[-1], ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(
                    f"{schema} round {index + 1}: success, {len(result['evidence_list'])} evidence records", flush=True
                )
            result = rounds[-1]["result"]
            text = result["result_markdown"].replace(",", "")
            expected = ("20", "14", "60") if schema == "behavior" else ("20000", "16000", "6.4", "25")
            assert all(number in text for number in expected), "Final report omitted a documented baseline metric"
            files = {}
            for kind, extension in (("markdown", "md"), ("html", "html")):
                post(client, user, f"/api/results/{rounds[-1]['task_id']}/export", {"format": kind})
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    response = client.get(
                        APP + f"/api/results/{rounds[-1]['task_id']}/download", params={"format": kind}
                    )
                    if response.status_code == 200:
                        break
                    time.sleep(0.5)
                assert response.status_code == 200
                files[kind] = response.content
                (output / f"{schema}-report.{extension}").write_bytes(response.content)
            assert files["markdown"].decode() == result["result_markdown"]
            assert b"<script" not in files["html"].lower() and b"https://" not in files["html"]
            response = client.post(
                APP + "/auth/logout", json={}, headers={"Origin": APP, "X-CSRF-Token": user["csrf_token"]}
            )
            assert response.status_code == 303
            client.cookies.clear()
            user = login(client)
            for kind, content in files.items():
                assert (
                    client.get(APP + f"/api/results/{rounds[-1]['task_id']}/download", params={"format": kind}).content
                    == content
                )
            ledger[schema] = {
                "conversation_id": cid,
                "task_ids": [r["task_id"] for r in rounds],
                "downloads": {k: hashlib.sha256(v).hexdigest() for k, v in files.items()},
            }
    ledger["usage"] = usage(tids)
    ledger["prior_cny_upper_estimate"] = prior_cny
    ledger["cumulative_cny_upper_estimate"] = prior_cny + ledger["usage"]["cny_upper_estimate"]
    ledger["model_id"] = model["model_id"]
    ledger["budget_cny"] = 66
    ledger["runtime_digest"] = runtime_digest()
    ledger["effective_context"] = context
    (output / "demos.json").write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(ledger, ensure_ascii=True), flush=True)


def verify_existing(output):
    ledger = json.loads((output / "demos.json").read_text(encoding="utf-8"))
    assert ledger["runtime_digest"] == runtime_digest(), (
        "Runtime changed; real demonstration evidence needs revalidation"
    )
    with httpx2.Client(verify=ssl.create_default_context(cafile=str(PACKAGE / "ca.crt")), timeout=30) as client:
        login(client)
        for schema in DEMOS:
            for index, tid in enumerate(ledger[schema]["task_ids"], 1):
                saved = json.loads((output / f"{schema}-round-{index}.json").read_text(encoding="utf-8"))
                result = client.get(APP + "/api/results/" + tid).json()
                assert result["result_markdown"] == saved["result"]["result_markdown"]
            for kind, digest in ledger[schema]["downloads"].items():
                content = client.get(APP + f"/api/results/{tid}/download", params={"format": kind})
                assert content.status_code == 200 and hashlib.sha256(content.content).hexdigest() == digest
    print("Revalidated both real demonstrations and all eight results; no additional model calls.")


if __name__ == "__main__":
    if sys.argv[1:] == ["--prepare"]:
        prepare()
    elif len(sys.argv) == 3 and sys.argv[1] == "--verify":
        verify_existing(Path(sys.argv[2]))
    else:
        demos(Path(sys.argv[1]))
