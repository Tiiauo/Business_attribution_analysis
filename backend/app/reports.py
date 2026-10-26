"""On-demand report exports and durable conversation deletion requests."""

from typing import Annotated, Literal

from fastapi import APIRouter, Path, Response
from pydantic import Field
from sqlalchemy import delete, select

from app.attachments import conversation_path, resources_ready
from app.auth import DB, now, owned_conversation
from app.chat import MAX_ID, Current, Identifier, Writer, identifier
from app.core.audit import record_event
from app.core.errors import ApiError
from app.models import AnalysisResult, AnalysisTask, WebsocketToken
from app.runtime import StrictModel
from app.task_events import event
from app.tasks import task_owner

router = APIRouter(prefix="/api")
Format = Literal["markdown", "html"]


def export_path(task, kind):
    return conversation_path(
        task.user_id, task.conversation_id, "exports", task.id, "report." + ("md" if kind == "markdown" else "html")
    )


async def export_state(db, task, result, kind, status, error=None):
    value = dict(result.export_status_json)
    value[kind] = {
        "status": status,
        "error_message": error,
        "generated_at": now().isoformat() if status == "success" else None,
    }
    result.export_status_json = value
    await event(db, task, "export_status", format=kind, status=status, error_message=error)


async def check_export_files(db, task, result):
    for kind, state in result.export_status_json.items():
        if state["status"] == "success":
            try:
                exists = export_path(task, kind).is_file()
            except (OSError, ValueError):
                exists = False
            if not exists:
                await export_state(db, task, result, kind, "failed", "导出文件缺失，请重新导出。")


class ExportRequest(StrictModel):
    format: Format


@router.post("/results/{task_id}/export")
async def export(
    task_id: Annotated[int, Path(ge=1, le=MAX_ID)], payload: ExportRequest, response: Response, db: DB, user: Writer
):
    task = await task_owner(db, task_id, user)
    await owned_conversation(task.conversation_id, user, db, lock=True)
    task = await task_owner(db, task_id, user, lock=True)
    if task.task_status != "success":
        raise ApiError(409, "RESULT_NOT_READY", "仅成功分析可导出。")
    result = await db.scalar(select(AnalysisResult).where(AnalysisResult.task_id == task.id).with_for_update())
    await check_export_files(db, task, result)
    state = result.export_status_json[payload.format]["status"]
    if state not in {"generating", "success"}:
        await resources_ready(db)
        await export_state(db, task, result, payload.format, "generating")
        state = "generating"
        record_event(
            db,
            "export_requested",
            operator_id=user.id,
            task_id=task.id,
            target_type="result",
            target_id=str(result.id),
            format=payload.format,
        )
    await db.commit()
    response.status_code = 200 if state == "success" else 202
    return {
        "format": payload.format,
        "status": state,
        "download_url": f"/api/results/{task.id}/download?format={payload.format}" if state == "success" else None,
    }


@router.get("/results/{task_id}/download")
async def download(task_id: Annotated[int, Path(ge=1, le=MAX_ID)], db: DB, user: Current, format: Format = "markdown"):
    task = await task_owner(db, task_id, user)
    await owned_conversation(task.conversation_id, user, db, lock=True)
    task = await task_owner(db, task_id, user, lock=True)
    result = await db.scalar(select(AnalysisResult).where(AnalysisResult.task_id == task.id).with_for_update())
    if not result:
        raise ApiError(409, "RESULT_NOT_READY", "结果尚未就绪。")
    await check_export_files(db, task, result)
    state = result.export_status_json[format]["status"]
    if state != "success":
        await db.commit()
        code = {"not_generated": "EXPORT_NOT_GENERATED", "generating": "EXPORT_IN_PROGRESS", "failed": "EXPORT_FAILED"}[
            state
        ]
        raise ApiError(409, code, "报告文件尚未就绪，请生成或重试导出。")
    try:
        content = export_path(task, format).read_bytes()
    except (OSError, ValueError):
        await export_state(db, task, result, format, "failed", "导出文件缺失，请重新导出。")
        await db.commit()
        raise ApiError(409, "EXPORT_FAILED", "导出文件缺失，请重新导出。") from None
    await db.commit()
    extension = "md" if format == "markdown" else "html"
    return Response(
        content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="report-{task.id}.{extension}"'},
    )


class DeleteConversations(StrictModel):
    conversation_ids: list[Identifier] = Field(min_length=1, max_length=100)


@router.post("/chat/delete", status_code=202)
async def delete_conversations(payload: DeleteConversations, db: DB, user: Writer):
    ids = sorted({identifier(value) for value in payload.conversation_ids})
    if len(ids) != len(payload.conversation_ids):
        raise ApiError(400, "INVALID_REQUEST", "会话 ID 不能重复。")
    rows = [await owned_conversation(cid, user, db, lock=True, include_deleted=True) for cid in ids]
    await resources_ready(db)
    items = []
    for row in rows:
        if row.status != "deleted" or row.cleanup_status == "failed":
            row.status, row.cleanup_status, row.cleanup_error, row.cleanup_updated_at = "deleted", "queued", None, now()
            tasks = (
                await db.scalars(
                    select(AnalysisTask)
                    .where(AnalysisTask.conversation_id == row.id, AnalysisTask.task_status.in_(("queued", "running")))
                    .with_for_update()
                )
            ).all()
            for task in tasks:
                task.cancel_requested_at = task.cancel_requested_at or now()
            await db.execute(delete(WebsocketToken).where(WebsocketToken.conversation_id == row.id))
            record_event(
                db,
                "conversation_delete_requested",
                operator_id=user.id,
                target_type="conversation",
                target_id=str(row.id),
            )
        items.append(
            {
                "conversation_id": str(row.id),
                "status": "deleted",
                "cleanup_status": row.cleanup_status,
                "cleanup_error": row.cleanup_error,
                "cleanup_updated_at": row.cleanup_updated_at,
                "can_retry": row.cleanup_status == "failed",
            }
        )
    await db.commit()
    return {"items": items}
