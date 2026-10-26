from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path
from pydantic import Field
from sqlalchemy import and_, exists, func, or_, select

from app.auth import DB, current_user, owned_conversation, write_user
from app.contracts import ConversationInfo, HistoryPage, Page
from app.core.audit import record_event
from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.security import safe_path
from app.models import AnalysisTask, Attachment, Conversation, Message, User
from app.runtime import StrictModel, runtime_row

router = APIRouter(prefix="/api/chat", tags=["conversations"])
Current = Annotated[User, Depends(current_user)]
Writer = Annotated[User, Depends(write_user)]
Identifier = Annotated[str, Field(strict=True, pattern=r"^[1-9][0-9]{0,18}$")]
MAX_ID = 2**63 - 1


def identifier(value: str) -> int:
    if not value.isascii() or not value.isdecimal() or value.startswith("0") or len(value) > 19:
        raise ApiError(400, "INVALID_REQUEST", "资源 ID 无效。")
    number = int(value)
    if not 0 < number <= MAX_ID:
        raise ApiError(400, "INVALID_REQUEST", "资源 ID 无效。")
    return number


class CreateConversation(StrictModel):
    title: str | None = Field(default=None, strict=True)
    data_source_id: Identifier | None = None


class UpdateConversation(StrictModel):
    conversation_id: Identifier
    title: str | None = Field(default=None, strict=True)
    data_source_id: Identifier | None = None


class ConversationRequest(StrictModel):
    conversation_id: Identifier


def title_value(value: str | None, *, creating=False) -> str:
    value = (value or "").strip()
    if len(value) > 100:
        raise ApiError(400, "FIELD_TOO_LONG", "标题最多 100 个 Unicode 码点。")
    if not value and not creating:
        raise ApiError(400, "INVALID_TITLE", "请输入非空会话标题。")
    return value or "新会话"


def conversations():
    locked = exists().where(Message.conversation_id == Conversation.id, Message.role == "user")
    tasks = select(AnalysisTask.id).where(AnalysisTask.conversation_id == Conversation.id)
    current = tasks.where(AnalysisTask.task_status.in_(("queued", "running"))).order_by(AnalysisTask.id.desc()).limit(1)
    latest = tasks.order_by(AnalysisTask.id.desc()).limit(1)
    return select(Conversation, locked, current.scalar_subquery(), latest.scalar_subquery())


def info(record, sources: dict) -> ConversationInfo:
    row, locked, current, latest = record
    deleting = row.status == "deleted"
    source = sources.get(str(row.data_source_id), {})
    return ConversationInfo(
        id=str(row.id),
        conversation_id=str(row.id),
        title="正在清理的会话" if deleting else row.title,
        title_source=row.title_source,
        status=row.status,
        data_source_id=str(row.data_source_id) if row.data_source_id and not deleting else None,
        data_source_locked=locked and not deleting,
        data_source_name=None if deleting else source.get("name"),
        data_source_status="unselected"
        if deleting
        else source.get("status", "not_published" if row.data_source_id else "unselected"),
        current_task_id=str(current) if current and not deleting else None,
        latest_task_id=str(latest) if latest and not deleting else None,
        cleanup_status=row.cleanup_status,
        cleanup_error=row.cleanup_error,
        cleanup_updated_at=row.cleanup_updated_at,
        can_retry=row.cleanup_status == "failed",
        last_message_at=row.last_message_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def metadata(db, conversation_id: int) -> ConversationInfo:
    record = (await db.execute(conversations().where(Conversation.id == conversation_id))).one()
    return info(record, (await runtime_row(db)).config_value["data_sources"])


async def enabled_source(db, source_id: str):
    source = (await runtime_row(db, lock=True)).config_value["data_sources"].get(source_id)
    if not source or source["status"] != "enabled":
        raise ApiError(409, "DATA_SOURCE_UNAVAILABLE", "数据源未启用，请读取最新列表后重新选择。")


@router.post("/create", response_model=ConversationInfo)
async def create(payload: CreateConversation, db: DB, user: Writer):
    title = title_value(payload.title, creating=True)
    source_id = identifier(payload.data_source_id) if payload.data_source_id else None
    if source_id:
        await enabled_source(db, payload.data_source_id)
    row = Conversation(
        user_id=user.id,
        title=title,
        title_source="manual" if payload.title and payload.title.strip() else "default",
        data_source_id=source_id,
    )
    db.add(row)
    await db.flush()
    record_event(db, "conversation_created", operator_id=user.id, target_type="conversation", target_id=str(row.id))
    await db.commit()
    return await metadata(db, row.id)


@router.post("/update", response_model=ConversationInfo)
async def update(payload: UpdateConversation, db: DB, user: Writer):
    row = await owned_conversation(identifier(payload.conversation_id), user, db, lock=True)
    fields = payload.model_fields_set - {"conversation_id"}
    if not fields:
        raise ApiError(400, "INVALID_REQUEST", "请提供标题或数据源。")
    title = title_value(payload.title) if "title" in fields else None
    if "data_source_id" in fields:
        if row.status != "active":
            raise ApiError(409, "CONVERSATION_ARCHIVED", "会话已归档，请先恢复。")
        if not payload.data_source_id:
            raise ApiError(400, "DATA_SOURCE_REQUIRED", "请选择启用的数据源。")
        source_id = identifier(payload.data_source_id)
        locked = (await metadata(db, row.id)).data_source_locked
        if locked and source_id != row.data_source_id:
            raise ApiError(409, "CONVERSATION_SOURCE_LOCKED", "数据源已绑定，更换范围请新建会话。")
        if not locked:
            await enabled_source(db, payload.data_source_id)
            row.data_source_id = source_id
    if title is not None:
        row.title, row.title_source = title, "manual"
    record_event(db, "conversation_updated", operator_id=user.id, target_type="conversation", target_id=str(row.id))
    await db.commit()
    return await metadata(db, row.id)


@router.get("/ls", response_model=Page[ConversationInfo])
async def listing(
    db: DB,
    user: Current,
    status: Literal["active", "archived", "deleted"] = "active",
    cursor: str | None = None,
    page_size: int = 50,
):
    if not 1 <= page_size <= 100:
        raise ApiError(400, "INVALID_CURSOR", "每页数量须为 1—100。")
    stamp = func.coalesce(Conversation.last_message_at, Conversation.created_at)
    query = conversations().where(Conversation.user_id == user.id, Conversation.status == status)
    if cursor:
        try:
            owner, state, timestamp, last_id = cursor.split("|")
            timestamp = datetime.fromisoformat(timestamp)
            last_id = int(last_id)
            if owner != str(user.id) or state != status or timestamp.tzinfo is None or not 0 < last_id <= MAX_ID:
                raise ValueError()
        except (ValueError, OverflowError):
            raise ApiError(400, "INVALID_CURSOR", "会话游标无效，请重新读取。") from None
        query = query.where(or_(stamp < timestamp, and_(stamp == timestamp, Conversation.id < last_id)))
    records = (await db.execute(query.order_by(stamp.desc(), Conversation.id.desc()).limit(page_size + 1))).all()
    sources = (await runtime_row(db)).config_value["data_sources"]
    items = [info(record, sources) for record in records[:page_size]]
    last = items[-1] if items else None
    next_cursor = None
    if len(records) > page_size:
        next_cursor = f"{user.id}|{status}|{(last.last_message_at or last.created_at).isoformat()}|{last.id}"
    return {"items": items, "next_cursor": next_cursor}


@router.post("/archive", response_model=ConversationInfo)
async def archive(payload: ConversationRequest, db: DB, user: Writer):
    row = await owned_conversation(identifier(payload.conversation_id), user, db, lock=True)
    if row.status == "active":
        if (await metadata(db, row.id)).current_task_id:
            raise ApiError(409, "CONVERSATION_BUSY", "请等待任务结束后再归档。")
        attachments = select(Attachment.id).where(Attachment.conversation_id == row.id)
        for state, code in (("deleting", "ATTACHMENT_DELETE_IN_PROGRESS"), ("failed", "ATTACHMENT_DELETE_FAILED")):
            if await db.scalar(attachments.where(Attachment.delete_status == state).limit(1)):
                raise ApiError(409, code, "请先完成附件删除或重试失败的清理，再归档。")
        if await db.scalar(attachments.where(Attachment.parse_status.in_(("pending", "parsing"))).limit(1)):
            raise ApiError(409, "ATTACHMENT_PARSE_IN_PROGRESS", "附件仍在解析，请等待结束后再归档。")
        row.status = "archived"
        record_event(
            db, "conversation_archived", operator_id=user.id, target_type="conversation", target_id=str(row.id)
        )
        await db.commit()
    return await metadata(db, row.id)


@router.post("/restore", response_model=ConversationInfo)
async def restore(payload: ConversationRequest, db: DB, user: Writer):
    row = await owned_conversation(identifier(payload.conversation_id), user, db, lock=True)
    if row.status == "archived":
        row.status = "active"
        record_event(
            db, "conversation_restored", operator_id=user.id, target_type="conversation", target_id=str(row.id)
        )
        await db.commit()
    return await metadata(db, row.id)


@router.get("/ls/{conversation_id}", response_model=HistoryPage)
async def history(
    conversation_id: Annotated[int, Path(ge=1, le=MAX_ID)],
    db: DB,
    user: Current,
    cursor: str | None = None,
    page_size: int = 50,
):
    row = await owned_conversation(conversation_id, user, db, include_deleted=True)
    if not 1 <= page_size <= 100:
        raise ApiError(400, "INVALID_CURSOR", "历史每页数量须为 1—100。")
    conversation = await metadata(db, row.id)
    if row.status == "deleted":
        return {"conversation": conversation, "items": [], "next_cursor": None, "snapshot_max_seq_no": 0}
    if cursor:
        try:
            owner, maximum, before = map(int, cursor.split("|"))
            if owner != row.id or not 0 < before <= maximum <= MAX_ID:
                raise ValueError()
        except (ValueError, OverflowError):
            raise ApiError(400, "INVALID_CURSOR", "历史游标无效或属于其他会话，请重新读取。") from None
    else:
        maximum = await db.scalar(select(func.max(Message.seq_no)).where(Message.conversation_id == row.id)) or 0
        before = maximum + 1
    records = (
        await db.execute(
            select(Message, AnalysisTask.attachment_snapshot_json)
            .join(AnalysisTask, AnalysisTask.id == Message.task_id)
            .where(
                Message.conversation_id == row.id,
                AnalysisTask.conversation_id == row.id,
                AnalysisTask.user_id == user.id,
                Message.seq_no <= maximum,
                Message.seq_no < before,
            )
            .order_by(Message.seq_no.desc())
            .limit(page_size + 1)
        )
    ).all()
    selected = records[:page_size]
    items = await message_items(db, row, user, list(reversed(selected)))
    return {
        "conversation": conversation,
        "items": items,
        "next_cursor": f"{row.id}|{maximum}|{selected[-1][0].seq_no}" if len(records) > page_size else None,
        "snapshot_max_seq_no": maximum,
    }


async def message_items(db, row, user, selected):
    from app.provenance import Sources

    sources = await Sources.load(db, row.id)
    attachment_ids = {
        identifier(item["attachment_id"])
        for message, snapshots in selected
        if message.role == "user"
        for item in snapshots
    }
    attachments = (
        {
            str(item.id): item
            for item in (
                await db.scalars(
                    select(Attachment).where(Attachment.conversation_id == row.id, Attachment.id.in_(attachment_ids))
                )
            ).all()
        }
        if attachment_ids
        else {}
    )
    items = []
    for message, snapshots in selected:
        refs = []
        for snapshot in snapshots if message.role == "user" else []:
            attachment = attachments.get(snapshot["attachment_id"])
            reason = "deleted"
            if attachment:
                reason = {"deleting": "deleting", "failed": "delete_failed"}.get(attachment.delete_status)
                if reason is None:
                    try:
                        parts = attachment.file_path.split("/")
                        expected = ["uploads", str(user.id), str(row.id)]
                        available = parts[:3] == expected and safe_path(get_settings().data_root, *parts).is_file()
                    except (ValueError, OSError):
                        available = False
                    reason = None if available else "storage_missing"
            refs.append(
                {
                    **snapshot,
                    "available": reason is None,
                    "unavailable_reason": reason,
                    "download_url": f"/api/attachment/get?attachment_id={snapshot['attachment_id']}"
                    if reason is None
                    else None,
                }
            )
        items.append(
            {
                "id": str(message.id),
                "message_id": str(message.id),
                "task_id": str(message.task_id),
                "role": message.role,
                "message_type": message.message_type,
                "content": message.content,
                "seq_no": message.seq_no,
                "message_status": message.message_status,
                "tool_name": message.tool_name,
                "tool_status": message.tool_status,
                "tool_call_id": message.tool_call_id,
                "tool_input": message.tool_input_json,
                "tool_outputs": [
                    sources.output_info(str(message.task_id), value) for value in message.tool_outputs_json
                ],
                "attachments": refs,
                "created_at": message.created_at,
                "updated_at": message.updated_at,
            }
        )
    return items
