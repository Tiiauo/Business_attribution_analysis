"""Operator smoke test: real configured model reads one explicitly selected attachment."""

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
        verify=ssl.create_default_context(cafile=str(settings.ca_file)), timeout=30, follow_redirects=False
    ) as client:
        user, _, _ = login(client, environment)
        headers = {"Origin": settings.public_url, "X-CSRF-Token": user["csrf_token"]}
        source = next(
            row
            for row in client.get(settings.public_url + "/api/data-sources").json()["items"]
            if row["schema"] == "market"
        )
        conversation = client.post(
            settings.public_url + "/api/chat/create",
            json={"title": "M05 附件真实模型联调", "data_source_id": source["id"]},
            headers=headers,
        ).json()
        upload = client.post(
            settings.public_url + "/api/attachment/upload",
            data={"conversation_id": conversation["id"], "has_header": "true"},
            files={
                "file": (
                    "m05-attachment.csv",
                    b"id,count\n00123,10\n00002,20",
                    "text/csv",
                )
            },
            headers=headers,
        )
        if upload.status_code != 202:
            raise RuntimeError("Attachment upload failed: " + upload.text)
        attachment = upload.json()
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            items = client.get(
                settings.public_url + "/api/attachment/ls",
                params={"conversation_id": conversation["id"]},
            ).json()["items"]
            attachment = next(row for row in items if row["id"] == attachment["id"])
            if attachment["parse_status"] in {"success", "failed"}:
                break
            time.sleep(0.5)
        if attachment["parse_status"] != "success":
            raise RuntimeError("Attachment parse failed: " + str(attachment.get("parse_error")))
        accepted = client.post(
            settings.public_url + "/api/chat/send",
            json={
                "conversation_id": conversation["id"],
                "data_source_id": source["id"],
                "client_request_id": "attachment-smoke",
                "content": (
                    "请只读取本轮显式选择的附件 m05-attachment.csv，调用 read_file 获取全部三条数据，"
                    "其中有效数据行的 count 为 10 和 20。不得查询数据库。提交六部分结果，"
                    "明确记录两行 count 的合计为 30、已跳过表头，并为该事实登记真实附件证据。"
                ),
                "task_kind": "analysis",
                "attachment_ids": [attachment["id"]],
            },
            headers=headers,
        )
        if accepted.status_code != 200:
            raise RuntimeError("Task submission failed: " + accepted.text)
        task_id = accepted.json()["task_id"]
        deadline = time.monotonic() + 610
        while time.monotonic() < deadline:
            snapshot = client.get(settings.public_url + "/api/tasks/" + task_id).json()
            if snapshot["task_status"] not in {"queued", "running"}:
                break
            time.sleep(1)
        else:
            raise RuntimeError("Attachment model smoke deadline exceeded")
        if snapshot["task_status"] != "success":
            raise RuntimeError(
                json.dumps(
                    {"task_id": task_id, "status": snapshot["task_status"], "error": snapshot["error_message"]},
                    ensure_ascii=True,
                )
            )
        result = client.get(settings.public_url + "/api/results/" + task_id).json()
        tools = [
            {"tool_name": message["tool_name"], "status": message["tool_status"]}
            for message in snapshot["messages"]
            if message["role"] == "tool"
        ]
        assert any(row["tool_name"] == "read_file" and row["status"] == "success" for row in tools), tools
        assert any(row["source_type"] == "file" for row in result["evidence_list"]), result["evidence_list"]
        values = {str(metric["metric_value"]) for metric in result["key_metrics"]}
        assert "30" in values, result["key_metrics"]
        print(
            json.dumps(
                {
                    "task_id": task_id,
                    "status": "success",
                    "attachment_id": attachment["id"],
                    "tools": tools,
                    "metrics": result["key_metrics"],
                    "evidence_ids": [row["evidence_id"] for row in result["evidence_list"]],
                    "conversation_id": conversation["id"],
                },
                ensure_ascii=True,
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
