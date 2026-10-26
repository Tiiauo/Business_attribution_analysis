import asyncio
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Path, WebSocket, WebSocketDisconnect
from sqlalchemy import exists, func, select, text

from app.auth import DB, current_session, now, owned_conversation
from app.chat import MAX_ID, ConversationRequest, Current, Writer, identifier, message_items, metadata
from app.contracts import ResultInfo, SendRequest, TaskSnapshot
from app.core.audit import record_event
from app.core.config import get_settings
from app.core.database import Session
from app.core.errors import ApiError
from app.core.security import digest, log_event, random_token, request_fingerprint, safe_path
from app.models import AnalysisResult, AnalysisTask, Attachment, Conversation, Message, WebsocketToken
from app.runtime import execution_config

router = APIRouter(prefix="/api")
UNFINISHED = ("queued", "running")
EMPTY_EXPORTS = {
    kind: {"status": "not_generated", "error_message": None, "generated_at": None} for kind in ("markdown", "html")
}


async def task_owner(db, task_id, user, *, lock=False):
    query = (
        select(AnalysisTask)
        .join(Conversation, Conversation.id == AnalysisTask.conversation_id)
        .where(
            AnalysisTask.id == task_id,
            AnalysisTask.user_id == user.id,
            Conversation.user_id == user.id,
            Conversation.status != "deleted",
        )
    )
    row = await db.scalar(query.with_for_update(of=AnalysisTask) if lock else query)
    if row is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问。")
    return row


async def accepted_response(db, task):
    conversation = await metadata(db, task.conversation_id)
    return {
        "message_id": str(task.message_id),
        "task_id": str(task.id),
        "task_status": task.task_status,
        "task_kind": task.task_kind,
        **conversation.model_dump(mode="json"),
    }


@router.post("/chat/send")
async def send(payload: SendRequest, db: DB, user: Writer):
    conversation_id = identifier(payload.conversation_id)
    source_id = identifier(payload.data_source_id)
    attachment_ids = [identifier(value) for value in payload.attachment_ids]
    conversation = await owned_conversation(conversation_id, user, db, lock=True)
    fingerprint = request_fingerprint(payload.model_dump())
    old = await db.scalar(
        select(AnalysisTask).where(
            AnalysisTask.conversation_id == conversation.id, AnalysisTask.client_request_id == payload.client_request_id
        )
    )
    if old:
        if old.request_fingerprint != fingerprint:
            raise ApiError(409, "IDEMPOTENCY_CONFLICT", "该请求 ID 已用于不同内容，请使用新的请求 ID。")
        return await accepted_response(db, old)
    if conversation.status != "active":
        raise ApiError(409, "CONVERSATION_ARCHIVED", "会话已归档，请先恢复。")
    if await db.scalar(
        select(AnalysisTask.id)
        .where(AnalysisTask.conversation_id == conversation.id, AnalysisTask.task_status.in_(UNFINISHED))
        .limit(1)
    ):
        raise ApiError(409, "CONVERSATION_BUSY", "本会话已有任务，取消完成或任务结束后才能继续。")
    for state, code in (("deleting", "ATTACHMENT_DELETE_IN_PROGRESS"), ("failed", "ATTACHMENT_DELETE_FAILED")):
        if await db.scalar(
            select(Attachment.id)
            .where(Attachment.conversation_id == conversation.id, Attachment.delete_status == state)
            .limit(1)
        ):
            raise ApiError(409, code, "请先完成或重试附件清理。")
    locked = await db.scalar(select(exists().where(Message.conversation_id == conversation.id, Message.role == "user")))
    if locked and conversation.data_source_id != source_id:
        raise ApiError(409, "CONVERSATION_SOURCE_LOCKED", "数据源已绑定，更换范围请新建会话。")
    await execution_config(db, payload.data_source_id)
    if payload.task_kind == "final_report" and attachment_ids:
        raise ApiError(400, "FINAL_REPORT_ATTACHMENTS_NOT_ALLOWED", "最终报告不接受新附件，请先进行普通分析。")
    snapshots, attachments = [], []
    if attachment_ids:
        attachments = (
            await db.scalars(
                select(Attachment).where(
                    Attachment.id.in_(attachment_ids), Attachment.conversation_id == conversation.id
                )
            )
        ).all()
        if len(attachments) != len(attachment_ids):
            raise ApiError(404, "RESOURCE_NOT_FOUND", "附件不存在或不可访问。")
        for attachment in attachments:
            if attachment.parse_status != "success":
                raise ApiError(409, "ATTACHMENT_NOT_READY", "所选附件尚未成功解析。")
            manifest = safe_path(
                get_settings().data_root,
                "workspace",
                str(user.id),
                str(conversation.id),
                "parsed",
                str(attachment.id),
                "manifest.json",
            )
            if not manifest.is_file():
                raise ApiError(409, "ATTACHMENT_INPUT_UNAVAILABLE", "附件安全输入尚不可用，请重新上传或等待解析完成。")
            snapshots.append(
                {
                    "attachment_id": str(attachment.id),
                    "file_name": attachment.file_name,
                    "file_type": attachment.file_type,
                    "file_size": attachment.file_size,
                    "uploaded_at": attachment.created_at.isoformat(),
                    "parse_options": attachment.parse_options_json,
                }
            )
    maximum = await db.scalar(select(func.max(Message.seq_no)).where(Message.conversation_id == conversation.id)) or 0
    scope = None
    if payload.task_kind == "final_report":
        previous = (
            await db.scalars(
                select(AnalysisTask)
                .where(AnalysisTask.conversation_id == conversation.id, AnalysisTask.task_kind == "analysis")
                .order_by(AnalysisTask.id)
            )
        ).all()
        sources = [str(task.id) for task in previous if task.task_status == "success"]
        if not sources:
            raise ApiError(409, "FINAL_REPORT_NO_BASE", "至少完成一轮成功分析后才能生成最终报告。")
        scope = {
            "source_task_ids": sources,
            "incomplete_task_ids": [str(task.id) for task in previous if task.task_status != "success"],
            "up_to_seq_no": maximum,
            "accepted_at": now().isoformat(),
        }
    message_id = await db.scalar(text("SELECT nextval(pg_get_serial_sequence('messages','id'))"))
    task = AnalysisTask(
        conversation_id=conversation.id,
        user_id=user.id,
        message_id=message_id,
        client_request_id=payload.client_request_id,
        request_fingerprint=fingerprint,
        input_text=payload.content,
        task_kind=payload.task_kind,
        attachment_snapshot_json=snapshots,
        final_report_scope_json=scope,
    )
    db.add(task)
    await db.flush()
    message = Message(
        id=message_id,
        conversation_id=conversation.id,
        task_id=task.id,
        role="user",
        message_type="user_input",
        content=payload.content,
        seq_no=maximum + 1,
        message_status="complete",
    )
    db.add(message)
    await db.flush()
    conversation.data_source_id = source_id
    conversation.last_message_at = now()
    if conversation.title_source == "default":
        title = " ".join(payload.content.split())
        conversation.title = title[:30] + ("…" if len(title) > 30 else "")
        conversation.title_source = "auto"
    for attachment in attachments:
        if attachment.message_id is None:
            attachment.message_id = message_id
    from app.task_events import status_event

    await status_event(db, task)
    record_event(db, "task_created", operator_id=user.id, target_type="task", target_id=str(task.id), task_id=task.id)
    await db.commit()
    return await accepted_response(db, task)


@router.post("/tasks/{task_id}/cancel")
async def cancel(task_id: Annotated[int, Path(ge=1, le=MAX_ID)], db: DB, user: Writer):
    from app.task_events import event, status_event

    task = await task_owner(db, task_id, user, lock=True)
    if task.task_status in UNFINISHED and task.cancel_requested_at is None:
        task.cancel_requested_at = now()
        if task.task_status == "queued":
            task.task_status, task.finished_at, task.current_step = "cancelled", now(), "已取消"
        else:
            task.current_step = "正在取消，等待执行停止"
        await status_event(db, task)
        if task.task_status == "cancelled":
            await event(db, task, "done", finished_at=task.finished_at)
        record_event(
            db,
            "task_cancel_requested",
            operator_id=user.id,
            target_type="task",
            target_id=str(task.id),
            task_id=task.id,
        )
        await db.commit()
    return {
        "task_id": str(task.id),
        "task_status": task.task_status,
        "cancel_requested_at": task.cancel_requested_at,
        "is_cancelling": task.cancel_requested_at is not None and task.task_status in UNFINISHED,
    }


@router.get("/tasks/{task_id}", response_model=TaskSnapshot)
async def snapshot(task_id: Annotated[int, Path(ge=1, le=MAX_ID)], user: Current):
    async with Session() as db:
        await db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        task = await task_owner(db, task_id, user)
        conversation = await db.get(Conversation, task.conversation_id)
        records = (
            await db.execute(
                select(Message, AnalysisTask.attachment_snapshot_json)
                .join(AnalysisTask, AnalysisTask.id == Message.task_id)
                .where(Message.task_id == task.id)
                .order_by(Message.seq_no)
            )
        ).all()
        result = await db.scalar(select(AnalysisResult).where(AnalysisResult.task_id == task.id))
        assistant = next((message for message, _ in records if message.role == "assistant"), None)
        return {
            "task_id": str(task.id),
            "conversation_id": str(task.conversation_id),
            "message_id": str(task.message_id),
            "assistant_message_id": str(assistant.id) if assistant else None,
            "task_status": task.task_status,
            "task_kind": task.task_kind,
            "current_step": task.current_step,
            "cancel_requested_at": task.cancel_requested_at,
            "is_cancelling": task.cancel_requested_at is not None and task.task_status in UNFINISHED,
            "config_version": task.config_version,
            "final_report_scope": task.final_report_scope_json,
            "result_id": str(result.id) if result else None,
            "messages": await message_items(db, conversation, user, records),
            "export_status": result.export_status_json if result else EMPTY_EXPORTS,
            "last_event_id": task.last_event_id,
            "created_at": task.created_at,
            "started_at": task.started_at,
            "finished_at": task.finished_at,
            "error_message": task.error_message,
        }


@router.post("/chat/ws-token")
async def ws_token(payload: ConversationRequest, db: DB, user: Writer):
    conversation = await owned_conversation(identifier(payload.conversation_id), user, db)
    token = random_token()
    db.add(
        WebsocketToken(
            user_id=user.id,
            conversation_id=conversation.id,
            token=digest(token),
            expires_at=now() + timedelta(seconds=60),
        )
    )
    await db.commit()
    return {"websocket_token": token, "expires_in": 60}


@router.get("/results/{task_id}", response_model=ResultInfo)
async def result(task_id: Annotated[int, Path(ge=1, le=MAX_ID)], db: DB, user: Current):
    from copy import deepcopy

    from app.analysis import availability

    task = await task_owner(db, task_id, user)
    if task.task_status != "success":
        code = {"failed": "TASK_FAILED", "cancelled": "TASK_CANCELLED"}.get(task.task_status, "RESULT_NOT_READY")
        raise ApiError(409, code, "任务尚无成功结果，请查看任务过程。")
    row = await db.scalar(select(AnalysisResult).where(AnalysisResult.task_id == task.id))
    if row is None:
        raise ApiError(500, "RESULT_MISSING", "任务已完成但结果记录缺失，请联系管理员。")
    evidence = deepcopy(row.evidence_list_json)
    for item in evidence:
        locator = item["source_locator"]
        available, reason = await availability(db, locator)
        locator.update(available=available, unavailable_reason=reason, availability_checked_at=now().isoformat())
    return {
        "result_id": str(row.id),
        "task_id": str(task.id),
        "conversation_id": str(task.conversation_id),
        "task_kind": task.task_kind,
        "final_report_scope": task.final_report_scope_json,
        "problem_definition": row.problem_definition,
        "key_metrics": row.key_metrics_json,
        "evidence_list": evidence,
        "conclusion_text": row.conclusion_text,
        "missing_data_text": row.missing_data_text,
        "next_action_text": row.next_action_text,
        "result_markdown": row.result_markdown,
        "export_status": row.export_status_json,
        "created_at": row.created_at,
    }


@router.websocket("/chat/ws/chat")
async def websocket(websocket: WebSocket, websocket_token: str, conversation_id: str):
    queue = None
    receiving = pending = None
    broker = websocket.app.state.notifications
    try:
        if websocket.headers.get("origin") != get_settings().public_url or len(websocket_token) > 128:
            raise ApiError(403, "WS_FORBIDDEN", "连接来源无效。")
        cid = identifier(conversation_id)
        async with Session() as db:
            user, _ = await current_session(websocket, db)
            await owned_conversation(cid, user, db)
            token = await db.scalar(
                select(WebsocketToken)
                .where(
                    WebsocketToken.token == digest(websocket_token),
                    WebsocketToken.user_id == user.id,
                    WebsocketToken.conversation_id == cid,
                    WebsocketToken.consumed_at.is_(None),
                    WebsocketToken.expires_at > now(),
                )
                .with_for_update()
            )
            if token is None:
                raise ApiError(403, "WS_TOKEN_INVALID", "连接令牌无效、已使用或过期。")
            token.consumed_at = now()
            await db.commit()
        queue = broker.subscribe(str(cid))
        await websocket.accept()
        await websocket.send_json(
            {"type": "subscription_ready", "conversation_id": str(cid), "created_at": now().isoformat()}
        )
        receiving = asyncio.create_task(websocket.receive())
        pending = asyncio.create_task(queue.get())
        checked = asyncio.get_running_loop().time()
        while True:
            done, _ = await asyncio.wait((receiving, pending), timeout=2, return_when=asyncio.FIRST_COMPLETED)
            if receiving in done:
                value = receiving.result()
                if value["type"] == "websocket.disconnect":
                    break
                if len(value.get("text", "")) > 1024 or value.get("bytes"):
                    await websocket.close(code=1008)
                    break
                receiving = asyncio.create_task(websocket.receive())
            if pending in done:
                value = pending.result()
                if value is None:
                    await websocket.close(code=1013)
                    break
                await websocket.send_json(value)
                pending = asyncio.create_task(queue.get())
            if asyncio.get_running_loop().time() - checked >= 5:
                async with Session() as db:
                    actor, _ = await current_session(websocket, db)
                    await owned_conversation(cid, actor, db)
                checked = asyncio.get_running_loop().time()
    except (ApiError, ValueError):
        await websocket.close(code=1008)
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception as error:
        log_event("websocket_failed", error_type=type(error).__name__)
        await websocket.close(code=1011)
    finally:
        for job in (receiving, pending):
            if job:
                job.cancel()
        await asyncio.gather(*(job for job in (receiving, pending) if job), return_exceptions=True)
        if queue is not None:
            broker.unsubscribe(str(cid), queue)
