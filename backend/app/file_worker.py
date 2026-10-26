"""File jobs share the analysis worker's database lease and per-conversation serialization."""

import asyncio
import html
import json
import re
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select

from app.attachments import conversation_path, original_path
from app.auth import now
from app.core.audit import record_event
from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.security import log_event, require_storage, safe_path
from app.models import AnalysisResult, AnalysisTask, Attachment, Conversation, TaskLog
from app.provenance import Sources
from app.reports import export_path, export_state
from app.runtime import runtime_row
from app.sandbox import run_script


async def remove_tree(path, deadline):
    if time.monotonic() >= deadline:
        raise TimeoutError("文件清理超过阶段时限")
    # Never follow a link, including links planted below a validated root.
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise ValueError("清理目录包含符号链接，须移除链接后重试")
    if not path.exists():
        return
    if path.is_dir():
        for child in path.iterdir():
            await remove_tree(safe_path(path, child.name), deadline)
        path.rmdir()
    else:
        path.unlink()
    await asyncio.sleep(0)


def render_html(result):
    parts = result.result_markdown.split("\n\n")
    body = "\n".join(
        f"<h2>{html.escape(p[3:])}</h2>" if p.startswith("## ") and "\n" not in p else f"<p>{html.escape(p)}</p>"
        for p in parts
    )
    return (
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">"
        "<title>经营归因分析报告</title><style>body{max-width:960px;margin:40px auto;padding:0 24px;font:16px/1.7 system-ui,sans-serif;color:#202124}"
        "p{white-space:pre-wrap;overflow-wrap:anywhere}h2{margin-top:32px}</style><body><h1>经营归因分析报告</h1>"
        f"<p>任务 #{result.task_id} · {result.created_at.astimezone(ZoneInfo('Asia/Shanghai')).isoformat()}</p>{body}</body></html>"
    )


class FileWorker:
    def __init__(self, sessions):
        self.sessions, self.jobs = sessions, {}

    async def recover(self):
        # Called only after the old sandbox processes have been confirmed gone.
        async with self.sessions.begin() as db:
            attachments = (await db.scalars(select(Attachment))).all()
            owners = {c.id: c.user_id for c in (await db.scalars(select(Conversation))).all()}
            for row in attachments:
                if row.delete_status == "deleting":
                    row.delete_status, row.delete_error = "failed", "ATTACHMENT_DELETE_INTERRUPTED：删除中断，请重试。"
                if row.parse_status in {"pending", "parsing"}:
                    for directory in ("parsed", "parsing"):
                        await remove_tree(
                            conversation_path(
                                owners[row.conversation_id], row.conversation_id, "workspace", directory, row.id
                            ),
                            time.monotonic() + 60,
                        )
                    row.parse_status, row.parse_error, row.updated_at = (
                        "failed",
                        "PARSE_INTERRUPTED：解析中断，请删除后重传。",
                        now(),
                    )
            for result in (await db.scalars(select(AnalysisResult))).all():
                task = await db.get(AnalysisTask, result.task_id)
                for kind, state in tuple(result.export_status_json.items()):
                    if state["status"] == "generating":
                        path = export_path(task, kind)
                        path.with_suffix(path.suffix + ".tmp").unlink(missing_ok=True)
                        await export_state(db, task, result, kind, "failed", "EXPORT_INTERRUPTED：导出中断，请重试。")
            known = {row.file_path for row in attachments}
            root = safe_path(get_settings().data_root, "uploads")
            if root.exists():
                for user in root.iterdir():
                    if not user.name.isdecimal():
                        continue
                    for cid in safe_path(root, user.name).iterdir():
                        if not cid.name.isdecimal():
                            continue
                        for file in safe_path(root, user.name, cid.name).iterdir():
                            relative = f"uploads/{user.name}/{cid.name}/{file.name}"
                            if (
                                re.fullmatch(r"[a-f0-9]{32}\.(csv|xlsx|txt|md|markdown)", file.name)
                                and relative not in known
                            ):
                                safe_path(root, user.name, cid.name, file.name).unlink()

    async def parse(self, cid, aid):
        async with self.sessions.begin() as db:
            conversation = await db.scalar(select(Conversation).where(Conversation.id == cid).with_for_update())
            row = await db.get(Attachment, aid)
            if (
                not conversation
                or conversation.status != "active"
                or not row
                or row.delete_status != "none"
                or row.parse_status != "pending"
            ):
                return
            row.parse_status, row.updated_at = "parsing", now()
            limits = (await runtime_row(db)).config_value["parse_limits"]
            content = original_path(row, conversation.user_id).read_bytes()
            payload = {"kind": row.file_type, "options": row.parse_options_json, "limits": limits}
            owner = conversation.user_id
        try:
            script = Path(__file__).with_name("attachment_parser.py").read_text(encoding="utf-8")
            value = json.loads(
                await run_script(
                    script,
                    payload,
                    f"attachment-{aid}",
                    limits["parse_timeout_seconds"],
                    source=content,
                    memory=limits["parse_max_memory_bytes"],
                )
            )
            if "error" in value:
                raise ApiError(422, "ATTACHMENT_PARSE_FAILED", value["error"])
            result = value["result"]
            async with self.sessions.begin() as db:
                conversation = await db.scalar(select(Conversation).where(Conversation.id == cid).with_for_update())
                row = await db.get(Attachment, aid)
                if (
                    not conversation
                    or conversation.status != "active"
                    or not row
                    or row.delete_status != "none"
                    or row.parse_status != "parsing"
                ):
                    return
                require_storage()
                staging = conversation_path(owner, cid, "workspace", "parsing", aid)
                await remove_tree(staging, time.monotonic() + 60)
                staging.mkdir(parents=True)
                files = []
                for index, item in enumerate(result["files"]):
                    extension = "json" if item["format"] == "json" else "txt"
                    name = f"{index}.{extension}"
                    content = json.dumps(item["rows"], ensure_ascii=False) if extension == "json" else item["text"]
                    safe_path(staging, name).write_text(content, encoding="utf-8")
                    metadata = {key: item[key] for key in ("position", "locations", "cells") if key in item}
                    files.append({"relative_path": name, "format": item["format"], **metadata})
                manifest = {
                    "attachment_id": str(aid),
                    "file_name": row.file_name,
                    "file_type": row.file_type,
                    "parse_options": row.parse_options_json,
                    "summary": result["summary"],
                    "files": files,
                }
                safe_path(staging, "manifest.json").write_text(
                    json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
                )
                target = conversation_path(owner, cid, "workspace", "parsed", aid)
                target.parent.mkdir(parents=True, exist_ok=True)
                await remove_tree(target, time.monotonic() + 60)
                staging.rename(target)
                row.parse_status, row.parse_error, row.updated_at = "success", None, now()
                record_event(
                    db,
                    "attachment_parsed",
                    operator_id=owner,
                    target_type="attachment",
                    target_id=str(aid),
                    status="success",
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            message = error.message if isinstance(error, ApiError) else "解析失败，请转换格式或拆分后重传。"
            if isinstance(error, ApiError) and error.code == "COMMAND_TIMEOUT":
                message = "PARSE_TIMEOUT：超过 parse_timeout_seconds，请拆分文件。"
            async with self.sessions.begin() as db:
                await db.scalar(select(Conversation).where(Conversation.id == cid).with_for_update())
                row = await db.get(Attachment, aid)
                if row and row.delete_status == "none" and row.parse_status == "parsing":
                    await remove_tree(conversation_path(owner, cid, "workspace", "parsing", aid), time.monotonic() + 60)
                    await remove_tree(conversation_path(owner, cid, "workspace", "parsed", aid), time.monotonic() + 60)
                    row.parse_status, row.parse_error, row.updated_at = "failed", message, now()
                    record_event(
                        db,
                        "attachment_parse_failed",
                        operator_id=owner,
                        target_type="attachment",
                        target_id=str(aid),
                        status="failed",
                    )

    async def delete_attachment(self, cid, aid, timeout):
        async with self.sessions() as db:
            row = await db.get(Attachment, aid)
            conversation = await db.get(Conversation, cid)
            if not row or not conversation or conversation.status == "deleted":
                return
            sources = await Sources.load(db, cid)
            owner = conversation.user_id
            original = original_path(row, owner)
        deadline = time.monotonic() + timeout
        await remove_tree(original, deadline)
        for directory in ("parsed", "parsing"):
            await remove_tree(conversation_path(owner, cid, "workspace", directory, aid), deadline)
        for tid in sources.tasks:
            await remove_tree(conversation_path(owner, cid, "workspace", "tasks", tid, "input", aid), deadline)
            manifest = conversation_path(owner, cid, "workspace", "tasks", tid, "input", "manifest.json")
            if manifest.is_file():
                value = json.loads(manifest.read_text(encoding="utf-8"))
                manifest.write_text(
                    json.dumps([item for item in value if item["attachment_id"] != str(aid)], ensure_ascii=False),
                    encoding="utf-8",
                )
        for (tid, oid), artifact in sources.outputs.items():
            if artifact.get("retention_kind", "input_copy") == "derived":
                continue
            try:
                remove = str(aid) in sources.dependencies({"type": "output", "task_id": tid, "output_id": oid})
            except (KeyError, ValueError, TypeError):
                remove = True
            if remove:
                await remove_tree(sources.output_path(tid, artifact), deadline)
        async with self.sessions.begin() as db:
            conversation = await db.scalar(select(Conversation).where(Conversation.id == cid).with_for_update())
            if conversation and conversation.status != "deleted":
                await db.execute(delete(Attachment).where(Attachment.id == aid, Attachment.delete_status == "deleting"))
                record_event(db, "attachment_deleted", operator_id=owner, target_type="attachment", target_id=str(aid))

    async def export(self, cid, tid, kind):
        async with self.sessions.begin() as db:
            conversation = await db.scalar(select(Conversation).where(Conversation.id == cid).with_for_update())
            task = await db.scalar(select(AnalysisTask).where(AnalysisTask.id == tid).with_for_update())
            result = await db.scalar(select(AnalysisResult).where(AnalysisResult.task_id == tid).with_for_update())
            if (
                not conversation
                or conversation.status == "deleted"
                or not result
                or result.export_status_json[kind]["status"] != "generating"
            ):
                return
            path = export_path(task, kind)
            require_storage()
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".tmp")
            try:
                content = result.result_markdown if kind == "markdown" else render_html(result)
                temporary.write_text(content, encoding="utf-8", newline="")
                temporary.replace(path)
                result.result_file_path = str(path.parent.relative_to(get_settings().data_root)).replace("\\", "/")
                await export_state(db, task, result, kind, "success")
                record_event(
                    db,
                    "export_generated",
                    operator_id=task.user_id,
                    task_id=task.id,
                    target_type="result",
                    target_id=str(result.id),
                    format=kind,
                )
            finally:
                temporary.unlink(missing_ok=True)

    async def cleanup_conversation(self, cid, timeout, analysis_jobs):
        async def phase(name):
            async with self.sessions.begin() as db:
                row = await db.scalar(select(Conversation).where(Conversation.id == cid).with_for_update())
                if row is None:
                    return None
                row.cleanup_status, row.cleanup_error, row.cleanup_updated_at = name, None, now()
                return row.user_id

        owner = await phase("stopping")
        async with self.sessions() as db:
            tids = (await db.scalars(select(AnalysisTask.id).where(AnalysisTask.conversation_id == cid))).all()
        jobs = [analysis_jobs[tid] for tid in tids if tid in analysis_jobs]
        for job in jobs:
            if not job.done() and not job.cancelling():
                job.cancel()
        if jobs:
            _, pending = await asyncio.wait(jobs, timeout=timeout)
            if pending:
                raise TimeoutError("stopping：未能确认分析进程已停止，请重试")
        await phase("deleting_files")
        deadline = time.monotonic() + timeout
        for directory in ("uploads", "exports", "workspace"):
            await remove_tree(conversation_path(owner, cid, directory), deadline)
        await phase("deleting_records")
        async with asyncio.timeout(timeout), self.sessions.begin() as db:
            row = await db.scalar(select(Conversation).where(Conversation.id == cid).with_for_update())
            if row:
                await db.execute(delete(TaskLog).where(TaskLog.task_id.in_(tids)))
                await db.delete(row)
                record_event(
                    db, "conversation_deleted", operator_id=owner, target_type="conversation", target_id=str(cid)
                )

    async def perform(self, mode, cid, identity, timeout, analysis_jobs):
        try:
            if mode == "parse":
                await self.parse(cid, identity)
            elif mode == "attachment":
                await self.delete_attachment(cid, identity, timeout)
            elif mode == "export":
                tid, kind = identity
                await self.export(cid, tid, kind)
            else:
                await self.cleanup_conversation(cid, timeout, analysis_jobs)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log_event("file_job_failed", mode=mode, error_type=type(error).__name__)
            async with self.sessions.begin() as db:
                conversation = await db.scalar(select(Conversation).where(Conversation.id == cid).with_for_update())
                if not conversation:
                    return
                if mode == "conversation":
                    conversation.cleanup_status, conversation.cleanup_error, conversation.cleanup_updated_at = (
                        "failed",
                        f"清理失败（{type(error).__name__}），请重试。",
                        now(),
                    )
                elif mode == "attachment":
                    row = await db.get(Attachment, identity)
                    if row:
                        row.delete_status, row.delete_error, row.updated_at = (
                            "failed",
                            f"附件清理失败（{type(error).__name__}），请重试。",
                            now(),
                        )
                elif mode == "export":
                    tid, kind = identity
                    task = await db.scalar(select(AnalysisTask).where(AnalysisTask.id == tid).with_for_update())
                    result = await db.scalar(
                        select(AnalysisResult).where(AnalysisResult.task_id == tid).with_for_update()
                    )
                    if task and result and conversation.status != "deleted":
                        await export_state(db, task, result, kind, "failed", "文件生成失败，请重试导出。")

    async def tick(self, analysis_jobs):
        for cid, (_mode, _identity, job, _stopping) in tuple(self.jobs.items()):
            if job.done():
                await asyncio.gather(job, return_exceptions=True)
                self.jobs.pop(cid)
        async with self.sessions() as db:
            config = (await runtime_row(db)).config_value
            timeout = config["tools"]["cleanup_step_timeout_seconds"]
            desired = {}
            for row in (
                await db.scalars(
                    select(Conversation).where(
                        Conversation.status == "deleted", Conversation.cleanup_status != "failed"
                    )
                )
            ).all():
                desired[row.id] = ("conversation", row.id)
            rows = (
                await db.scalars(
                    select(Attachment).where(Attachment.delete_status == "deleting").order_by(Attachment.id)
                )
            ).all()
            for row in rows:
                desired.setdefault(row.conversation_id, ("attachment", row.id))
            for cid, (mode, identity, job, stopping) in tuple(self.jobs.items()):
                if cid in desired and (mode, identity) != desired[cid]:
                    if not job.cancelling():
                        job.cancel()
                    stopping = stopping or time.monotonic()
                    self.jobs[cid] = (mode, identity, job, stopping)
                    if time.monotonic() - stopping > timeout:
                        async with self.sessions.begin() as write:
                            row = await write.get(Conversation, cid, with_for_update=True)
                            if row and row.status == "deleted":
                                row.cleanup_status, row.cleanup_error, row.cleanup_updated_at = (
                                    "failed",
                                    "stopping：未确认文件执行进程停止，请重试。",
                                    now(),
                                )
                            elif desired[cid][0] == "attachment":
                                row = await write.get(Attachment, desired[cid][1])
                                if row:
                                    row.delete_status, row.delete_error = (
                                        "failed",
                                        "stopping：解析进程尚未停止，请重试。",
                                    )
            for result, task in (
                await db.execute(
                    select(AnalysisResult, AnalysisTask).join(AnalysisTask, AnalysisTask.id == AnalysisResult.task_id)
                )
            ).all():
                for kind, state in result.export_status_json.items():
                    if state["status"] == "generating":
                        desired.setdefault(task.conversation_id, ("export", (task.id, kind)))
            for row in (
                await db.scalars(
                    select(Attachment)
                    .where(Attachment.parse_status == "pending", Attachment.delete_status == "none")
                    .order_by(Attachment.id)
                )
            ).all():
                desired.setdefault(row.conversation_id, ("parse", row.id))
        for cid, (mode, identity) in desired.items():
            if cid not in self.jobs and len(self.jobs) < 3:
                self.jobs[cid] = (
                    mode,
                    identity,
                    asyncio.create_task(self.perform(mode, cid, identity, timeout, analysis_jobs)),
                    None,
                )

    async def stop(self):
        for _, _, job, _ in self.jobs.values():
            if not job.cancelling():
                job.cancel()
        await asyncio.gather(*(row[2] for row in self.jobs.values()), return_exceptions=True)
