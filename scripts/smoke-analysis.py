"""Operator smoke test: real configured model, three persisted rounds, no fabricated fallback."""

import json
import ssl
import sys
import time
from pathlib import Path

import httpx2

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests")]
from test_auth import login  # noqa: E402

from app.core.config import get_settings  # noqa: E402


def main():
    settings = get_settings()
    environment = {"app": settings.public_url, "idp": settings.issuer_url}
    with httpx2.Client(
        verify=ssl.create_default_context(cafile=str(settings.ca_file)), timeout=20, follow_redirects=False
    ) as client:
        user, _, _ = login(client, environment)
        headers = {"Origin": settings.public_url, "X-CSRF-Token": user["csrf_token"]}

        def post(path, payload):
            response = client.post(settings.public_url + path, json=payload, headers=headers)
            if response.status_code != 200:
                raise RuntimeError("Request failed: " + response.json().get("code", "unknown"))
            return response.json()

        source = next(
            row
            for row in client.get(settings.public_url + "/api/data-sources").json()["items"]
            if row["schema"] == "market"
        )
        conversation = post("/api/chat/create", {"title": "M05 真实模型联调", "data_source_id": source["id"]})
        rounds = []
        for index, (kind, content) in enumerate(
            [
                (
                    "analysis",
                    "请实际查询绑定数据源市场订单表的记录总行数，只计算COUNT(*)，以market全部记录为范围，不加时间/状态过滤。列一项指标、真实查询证据、结论、局限及至少两条具体建议，保持简短。",
                ),
                (
                    "analysis",
                    "沿用上一轮已登记的记录数和证据，解释总记录数为何不等于有效支付订单数。本轮不要重新查询，不要编造有效支付数量。保持简短。",
                ),
                (
                    "final_report",
                    "请汇总此前各成功轮次，明确区分记录行数与有效支付订单数，保留证据和未验证事项。不要重新查询。保持简短。",
                ),
            ]
        ):
            accepted = post(
                "/api/chat/send",
                {
                    "conversation_id": conversation["id"],
                    "data_source_id": source["id"],
                    "client_request_id": f"smoke-{index}",
                    "content": content,
                    "task_kind": kind,
                    "attachment_ids": [],
                },
            )
            task_id = accepted["task_id"]
            print(json.dumps({"task_id": task_id, "stage": "accepted", "kind": kind}), flush=True)
            deadline = time.monotonic() + 610
            while time.monotonic() < deadline:
                snapshot = client.get(settings.public_url + "/api/tasks/" + task_id).json()
                if snapshot["task_status"] not in {"queued", "running"}:
                    break
                time.sleep(1)
            else:
                raise RuntimeError("Smoke deadline exceeded")
            if snapshot["task_status"] != "success":
                print(
                    json.dumps(
                        {"task_id": task_id, "status": snapshot["task_status"], "error": snapshot["error_message"]},
                        ensure_ascii=True,
                    ),
                    flush=True,
                )
                raise RuntimeError("Real analysis did not succeed")
            result = client.get(settings.public_url + "/api/results/" + task_id).json()
            tools = [
                {"tool_name": m["tool_name"], "status": m["tool_status"]}
                for m in snapshot["messages"]
                if m["role"] == "tool"
            ]
            assert result["evidence_list"], "The real query evidence must survive subsequent rounds"
            if index:
                assert not tools, "Evidence-only follow-up/final report must not retrieve new data"
            rounds.append(
                {
                    "task_id": task_id,
                    "status": "success",
                    "kind": kind,
                    "tools": tools,
                    "metrics": result["key_metrics"],
                    "evidence_ids": [e["evidence_id"] for e in result["evidence_list"]],
                    "scope": result["final_report_scope"],
                }
            )
            print(json.dumps(rounds[-1], ensure_ascii=True), flush=True)
        assert rounds[-1]["scope"]["source_task_ids"] == [row["task_id"] for row in rounds[:2]]
        print("Real model smoke passed; conversation_id=" + conversation["id"], flush=True)


if __name__ == "__main__":
    main()
