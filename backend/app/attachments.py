"""Owned attachment HTTP boundary; only the worker parses or removes stored inputs."""

import json
import re
from pathlib import Path
from typing import Annotated
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import func, select, text
from starlette.datastructures import UploadFile

from app.auth import DB, now, owned_conversation
from app.chat import Current, Identifier, Writer, identifier
from app.contracts import AttachmentInfo, AttachmentPage
from app.core.audit import record_event
from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.security import require_storage, safe_path
from app.models import AnalysisTask, Attachment
from app.runtime import StrictModel

router = APIRouter(prefix="/api/attachment", tags=["attachments"])
RESOURCE_READY_LOCK = 743043


async def resources_ready(db):
    await db.execute(text("SELECT pg_advisory_xact_lock_shared(743044::bigint)"))
    ready = await db.scalar(
        text(
            "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype='advisory' "
            "AND objid=743043 AND classid=0 AND granted "
            "AND database=(SELECT oid FROM pg_database WHERE datname=current_database()))"
        )
    )
    if not ready:
        raise ApiError(503, "FILE_WORKER_NOT_READY", "文件执行器尚未完成启动恢复，请稍后重试。")


def conversation_path(user_id, cid, directory="workspace", *parts):
    return safe_path(get_settings().data_root, directory, str(user_id), str(cid), *map(str, parts))


async def writable(db, conversation, *, deleting_id=None):
    if conversation.status != "active":
        raise ApiError(409, "CONVERSATION_ARCHIVED", "会话已归档，请先恢复。")
    if await db.scalar(
        select(AnalysisTask.id)
        .where(AnalysisTask.conversation_id == conversation.id, AnalysisTask.task_status.in_(("queued", "running")))
        .limit(1)
    ):
        raise ApiError(409, "CONVERSATION_BUSY", "分析尚未结束，请等待后再操作附件。")
    query = select(Attachment).where(Attachment.conversation_id == conversation.id, Attachment.delete_status != "none")
    if deleting_id is not None:
        query = query.where(Attachment.id != deleting_id)
    blocked = await db.scalar(query.order_by(Attachment.id).limit(1))
    if blocked:
        code = "ATTACHMENT_DELETE_IN_PROGRESS" if blocked.delete_status == "deleting" else "ATTACHMENT_DELETE_FAILED"
        raise ApiError(409, code, "请先完成或重试已有附件清理。")


async def attachment_owner(db, aid, user, *, lock=False):
    row = await db.get(Attachment, identifier(aid))
    if not row:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问。")
    conversation = await owned_conversation(row.conversation_id, user, db, lock=lock)
    if lock:
        row = await db.scalar(
            select(Attachment).where(Attachment.id == row.id).execution_options(populate_existing=True)
        )
        if not row:
            raise ApiError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问。")
    return row, conversation


def original_path(row, user_id):
    parts = row.file_path.split("/")
    if parts[:3] != ["uploads", str(user_id), str(row.conversation_id)] or len(parts) != 4:
        raise ValueError("Invalid original path")
    return safe_path(get_settings().data_root, *parts)


def attachment_info(row, user_id):
    summary = None
    if row.parse_status == "success" and row.delete_status == "none":
        try:
            manifest = conversation_path(user_id, row.conversation_id, "workspace", "parsed", row.id, "manifest.json")
            summary = json.loads(manifest.read_text(encoding="utf-8"))["summary"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return {
        "id": str(row.id),
        "attachment_id": str(row.id),
        "conversation_id": str(row.conversation_id),
        "message_id": str(row.message_id) if row.message_id else None,
        "file_name": row.file_name,
        "file_type": row.file_type,
        "file_path": row.file_path,
        "file_size": row.file_size,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "parse_status": row.parse_status,
        "parse_error": row.parse_error,
        "parse_options": row.parse_options_json,
        "parse_summary": summary,
        "delete_status": row.delete_status,
        "delete_error": row.delete_error,
        "can_retry": row.delete_status == "failed",
        "download_url": f"/api/attachment/get?attachment_id={row.id}" if row.delete_status == "none" else None,
    }


@router.post("/upload", response_model=AttachmentInfo, status_code=202)
async def upload(request: Request, db: DB, user: Writer):
    await resources_ready(db)
    require_storage()
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > 20_256_000:
            raise ApiError(413, "FILE_TOO_LARGE", "单文件最多20 MB。")
        chunks.append(chunk)
    request._body = b"".join(chunks)
    async with request.form(max_files=1, max_fields=2, max_part_size=256000) as form:
        if len(form.multi_items()) != len(form) or set(form) - {"file", "conversation_id", "has_header"}:
            raise ApiError(400, "INVALID_REQUEST", "上传字段重复或不受支持。")
        file, cid = form.get("file"), form.get("conversation_id")
        if not isinstance(file, UploadFile) or not isinstance(cid, str):
            raise ApiError(400, "INVALID_REQUEST", "请选择文件和会话。")
        name = file.filename or ""
        if not name or len(name) > 255 or re.search(r"[\\/:\x00-\x1f\x7f]", name) or name in {".", ".."}:
            raise ApiError(400, "INVALID_FILE_NAME", "文件名无效，请移除路径及控制字符。")
        kind = Path(name).suffix.lower().lstrip(".")
        if kind not in {"csv", "xlsx", "txt", "md", "markdown"}:
            raise ApiError(400, "FILE_TYPE_UNSUPPORTED", "请选择 CSV、XLSX、TXT 或 Markdown。")
        options = {}
        if kind in {"csv", "xlsx"}:
            header = form.get("has_header", "true")
            if header not in {"true", "false"}:
                raise ApiError(400, "INVALID_TABLE_OPTIONS", "has_header 仅接受 true 或 false。")
            options = {"has_header": header == "true"}
        elif "has_header" in form:
            raise ApiError(400, "INVALID_TABLE_OPTIONS", "文本不适用表头选项。")
        content = await file.read(20_000_001)
        if len(content) > 20_000_000:
            raise ApiError(413, "FILE_TOO_LARGE", "单文件最多20 MB。")
    conversation = await owned_conversation(identifier(cid), user, db, lock=True)
    await resources_ready(db)
    await writable(db, conversation)
    total = await db.scalar(
        select(func.coalesce(func.sum(Attachment.file_size), 0)).where(Attachment.conversation_id == conversation.id)
    )
    if total + len(content) > 200_000_000:
        raise ApiError(413, "CONVERSATION_QUOTA_EXCEEDED", "会话附件累计最多200 MB，删除失败项仍计入配额。")
    stored = uuid4().hex + "." + kind
    path = conversation_path(user.id, conversation.id, "uploads", stored)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(content)
        row = Attachment(
            conversation_id=conversation.id,
            file_name=name,
            file_path=f"uploads/{user.id}/{conversation.id}/{stored}",
            file_type=kind,
            file_size=len(content),
            parse_options_json=options,
        )
        db.add(row)
        await db.flush()
        record_event(
            db,
            "attachment_uploaded",
            operator_id=user.id,
            target_type="attachment",
            target_id=str(row.id),
            file_size=len(content),
        )
        value = attachment_info(row, user.id)
        await db.commit()
        return value
    except BaseException:
        path.unlink(missing_ok=True)
        raise


@router.get("/ls", response_model=AttachmentPage)
async def listing(
    db: DB,
    user: Current,
    conversation_id: str,
    attachment_ids: Annotated[list[str] | None, Query()] = None,
    cursor: str | None = None,
    page_size: int = 50,
):
    conversation = await owned_conversation(identifier(conversation_id), user, db)
    if not 1 <= page_size <= 100:
        raise ApiError(400, "INVALID_CURSOR", "每页数量须为1—100。")
    query = select(Attachment).where(Attachment.conversation_id == conversation.id)
    if attachment_ids is not None:
        if len(attachment_ids) > 100 or len(set(attachment_ids)) != len(attachment_ids):
            raise ApiError(400, "INVALID_ATTACHMENT_IDS", "附件查询最多100个且不能重复。")
        query = query.where(Attachment.id.in_([identifier(aid) for aid in attachment_ids]))
    if cursor:
        try:
            owner, before = cursor.split("|")
            if owner != conversation_id:
                raise ValueError()
            query = query.where(Attachment.id < identifier(before))
        except (ValueError, ApiError):
            raise ApiError(400, "INVALID_CURSOR", "附件游标无效，请重新读取。") from None
    rows = (await db.scalars(query.order_by(Attachment.id.desc()).limit(page_size + 1))).all()
    return {
        "items": [attachment_info(row, user.id) for row in rows[:page_size]],
        "next_cursor": f"{conversation_id}|{rows[page_size - 1].id}" if len(rows) > page_size else None,
        "total_bytes": await db.scalar(
            select(func.coalesce(func.sum(Attachment.file_size), 0)).where(
                Attachment.conversation_id == conversation.id
            )
        ),
    }


class DeleteAttachment(StrictModel):
    attachment_id: Identifier


@router.post("/delete", status_code=202, response_model=AttachmentInfo)
async def delete(payload: DeleteAttachment, db: DB, user: Writer):
    row, conversation = await attachment_owner(db, payload.attachment_id, user, lock=True)
    await resources_ready(db)
    await writable(db, conversation, deleting_id=row.id)
    row.delete_status, row.delete_error, row.updated_at = "deleting", None, now()
    record_event(
        db, "attachment_delete_requested", operator_id=user.id, target_type="attachment", target_id=str(row.id)
    )
    await db.commit()
    return attachment_info(row, user.id)


@router.get("/get")
async def download(attachment_id: str, db: DB, user: Current):
    row, _ = await attachment_owner(db, attachment_id, user, lock=True)
    if row.delete_status != "none":
        code = "ATTACHMENT_DELETE_IN_PROGRESS" if row.delete_status == "deleting" else "ATTACHMENT_DELETE_FAILED"
        raise ApiError(409, code, "附件正在删除或清理失败，不可下载。")
    try:
        content = original_path(row, user.id).read_bytes()
    except (OSError, ValueError):
        raise ApiError(404, "ATTACHMENT_STORAGE_MISSING", "原件已不可用，请删除后重新上传。") from None
    return Response(
        content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(row.file_name, safe='')}"},
    )
