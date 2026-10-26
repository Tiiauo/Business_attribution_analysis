"""One durable PostgreSQL queue; only this process runs model and tool calls."""

import asyncio
import sys
import traceback
from contextlib import asynccontextmanager

import asyncpg
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import now
from app.core.audit import record_event
from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.security import log_event
from app.models import AnalysisResult, AnalysisTask, Conversation, Message, User
from app.runtime import execution_config
from app.task_events import database_dsn, event, status_event

LEADER_LOCK = 743042


class TaskContext:
    def __init__(self, sessions, task_id):
        self.sessions, self.task_id = sessions, task_id

    @asynccontextmanager
    async def mutation(self, *, terminal=False):
        async with self.sessions.begin() as db:
            cid = await db.scalar(select(AnalysisTask.conversation_id).where(AnalysisTask.id == self.task_id))
            conversation = await db.scalar(select(Conversation).where(Conversation.id == cid).with_for_update())
            task = await db.scalar(select(AnalysisTask).where(AnalysisTask.id == self.task_id).with_for_update())
            if not conversation or conversation.status == "deleted" or not task or task.task_status != "running":
                raise asyncio.CancelledError()
            if task.cancel_requested_at and not terminal:
                raise asyncio.CancelledError()
            yield db, task, conversation

    async def append(self, content):
        # Small committed chunks also stay below PostgreSQL's NOTIFY payload limit.
        for offset in range(0, len(content), 800):
            chunk = content[offset : offset + 800]
            async with self.mutation() as (db, task, _):
                message = await db.scalar(
                    select(Message).where(Message.task_id == task.id, Message.role == "assistant")
                )
                message.content += chunk
                await event(
                    db, task, "message_delta", message_id=str(message.id), seq_no=message.seq_no, delta_text=chunk
                )

    async def tool_start(self, name, call_id, arguments):
        async with self.mutation() as (db, task, conversation):
            seq = (
                await db.scalar(select(func.max(Message.seq_no)).where(Message.conversation_id == conversation.id))
            ) + 1
            message = Message(
                conversation_id=conversation.id,
                task_id=task.id,
                role="tool",
                message_type="tool_call",
                content="正在执行",
                seq_no=seq,
                message_status="streaming",
                tool_status="running",
                tool_name=name,
                tool_call_id=call_id,
                tool_input_json=arguments,
            )
            db.add(message)
            await db.flush()
            conversation.last_message_at = now()
            task.current_step = ("执行工具：" + name)[:200]
            await event(
                db,
                task,
                "tool_start",
                message_id=str(message.id),
                seq_no=seq,
                tool_name=name,
                tool_call_id=call_id,
                tool_status="running",
            )
            await status_event(db, task)
            record_event(
                db,
                "tool_started",
                operator_id=None,
                task_id=task.id,
                target_type="tool",
                target_id=call_id,
                tool_name=name,
            )

    async def tool_finish(self, call_id, summary, *, outputs=None, failed=False):
        async with self.mutation() as (db, task, _):
            message = await db.scalar(
                select(Message).where(Message.task_id == task.id, Message.tool_call_id == call_id)
            )
            message.content, message.tool_outputs_json = summary, outputs or []
            message.tool_status = "failed" if failed else "success"
            message.message_status = "failed" if failed else "complete"
            await event(
                db,
                task,
                "tool_finish",
                message_id=str(message.id),
                seq_no=message.seq_no,
                tool_name=message.tool_name,
                tool_call_id=call_id,
                tool_status=message.tool_status,
                tool_result_summary=summary[:800],
            )
            record_event(
                db,
                "tool_finished",
                operator_id=None,
                task_id=task.id,
                target_type="tool",
                target_id=call_id,
                tool_name=message.tool_name,
                status=message.tool_status,
            )

    async def complete(self, output):
        from app.analysis import render_markdown, snapshot_sources

        async with self.mutation(terminal=True) as (db, task, _):
            if task.cancel_requested_at:
                return await self._finish(db, task, "cancelled", None)
            output = await snapshot_sources(db, output)
            value = output.model_dump(mode="json", by_alias=True)
            result = AnalysisResult(
                task_id=task.id,
                conversation_id=task.conversation_id,
                problem_definition=value["problem_definition"],
                key_metrics_json=value["key_metrics"],
                evidence_list_json=value["evidence_list"],
                conclusion_text=value["conclusion_text"],
                missing_data_text=value["missing_data_text"],
                next_action_text=value["next_action_text"],
                result_markdown=render_markdown(output),
                created_at=now(),
            )
            db.add(result)
            await db.flush()
            await event(db, task, "result_ready", result_id=str(result.id))
            await self._finish(db, task, "success", None)

    async def finish(self, state, error=None):
        async with self.mutation(terminal=True) as (db, task, _):
            await self._finish(db, task, "cancelled" if task.cancel_requested_at else state, error)

    async def _finish(self, db, task, state, error):
        task.task_status, task.finished_at = state, now()
        task.current_step = {"success": "分析完成", "failed": "执行失败", "cancelled": "已取消"}[state]
        task.error_message = error.message if error and state == "failed" else None
        messages = (
            await db.scalars(select(Message).where(Message.task_id == task.id, Message.message_status == "streaming"))
        ).all()
        for message in messages:
            message.message_status = "complete" if state == "success" else state
            if message.role == "tool":
                message.tool_status = "failed" if state == "failed" else "cancelled"
                await event(
                    db,
                    task,
                    "tool_finish",
                    message_id=str(message.id),
                    seq_no=message.seq_no,
                    tool_name=message.tool_name,
                    tool_call_id=message.tool_call_id,
                    tool_status=message.tool_status,
                    tool_result_summary="执行已停止",
                )
        if error and state == "failed":
            await event(db, task, "error", error_message=error.message, code=error.code)
        await status_event(db, task)
        await event(db, task, "done", finished_at=task.finished_at)
        record_event(
            db,
            "task_finished",
            operator_id=None,
            task_id=task.id,
            target_type="task",
            target_id=str(task.id),
            status=state,
            code=error.code if error else None,
        )


async def claim(sessions):
    async with sessions.begin() as db:
        if (
            await db.scalar(select(func.count()).select_from(AnalysisTask).where(AnalysisTask.task_status == "running"))
        ) >= 3:
            return None
        candidate = await db.scalar(
            select(AnalysisTask)
            .where(AnalysisTask.task_status == "queued")
            .order_by(AnalysisTask.created_at, AnalysisTask.id)
            .limit(1)
        )
        if candidate is None:
            return None
        conversation = await db.scalar(
            select(Conversation).where(Conversation.id == candidate.conversation_id).with_for_update()
        )
        task = await db.scalar(
            select(AnalysisTask)
            .where(AnalysisTask.id == candidate.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if task.task_status != "queued":
            return None
        try:
            user = await db.get(User, task.user_id)
            if user.status != "active" or conversation.status != "active":
                raise ApiError(409, "TASK_INPUT_UNAVAILABLE", "账户或会话已停用，排队任务未执行。")
            version, config = await execution_config(db, str(conversation.data_source_id))
        except ApiError as error:
            task.task_status, task.finished_at, task.current_step = "failed", now(), "启动校验失败"
            task.error_message = error.message
            await event(db, task, "error", error_message=error.message, code=error.code)
            await status_event(db, task)
            await event(db, task, "done", finished_at=task.finished_at)
            return None
        task.config_version, task.config_snapshot_json = version, config
        task.task_status, task.started_at, task.current_step = "running", now(), "准备分析"
        seq = (await db.scalar(select(func.max(Message.seq_no)).where(Message.conversation_id == conversation.id))) + 1
        message = Message(
            conversation_id=conversation.id,
            task_id=task.id,
            role="assistant",
            message_type="assistant_text",
            seq_no=seq,
            message_status="streaming",
            content="",
        )
        db.add(message)
        await db.flush()
        conversation.last_message_at = now()
        await event(
            db,
            task,
            "message_start",
            message_id=str(message.id),
            seq_no=seq,
            user_message_id=str(task.message_id),
            message_status="streaming",
        )
        await status_event(db, task)
        record_event(
            db,
            "task_started",
            operator_id=None,
            task_id=task.id,
            target_type="task",
            target_id=str(task.id),
            config_version=version,
        )
        return task.id, config["tools"]["task_timeout_seconds"]


async def execute(context, timeout, executor):
    try:
        async with asyncio.timeout(timeout):
            output = await executor(context)
        await context.complete(output)
    except asyncio.CancelledError:
        await context.finish(
            "failed", ApiError(500, "TASK_INTERRUPTED", "执行器已停止，任务未完成；已保存过程可查看。")
        )
        raise
    except TimeoutError:
        await context.finish("failed", ApiError(504, "TASK_TIMEOUT", "任务超过运行时限，请缩小范围后重试。"))
    except ApiError as error:
        await context.finish("failed", error)
    except Exception as error:
        log_event(
            "task_executor_failed",
            error_type=type(error).__name__,
            task_id=context.task_id,
            locations=[f"{frame.name}:{frame.lineno}" for frame in traceback.extract_tb(error.__traceback__)],
        )
        await context.finish("failed", ApiError(500, "ANALYSIS_FAILED", "分析执行失败，已保存的过程仍可查看。"))


async def serve(executor=None):
    if executor is None:
        from app.analysis import run_analysis

        executor = run_analysis
    leader = await asyncpg.connect(database_dsn(), timeout=5)
    if not await leader.fetchval("SELECT pg_try_advisory_lock($1::bigint)", LEADER_LOCK):
        await leader.close()
        raise RuntimeError("Another task worker owns this database")
    engine = create_async_engine(get_settings().database_url.get_secret_value(), pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    jobs = {}
    from app.file_worker import FileWorker

    files = FileWorker(sessions)
    try:
        from app.sandbox import cleanup_orphans

        await leader.execute("SELECT pg_advisory_lock(743044::bigint)")
        await cleanup_orphans()
        await files.recover()
        async with sessions() as db:
            interrupted = (await db.scalars(select(AnalysisTask.id).where(AnalysisTask.task_status == "running"))).all()
        for task_id in interrupted:
            try:
                await TaskContext(sessions, task_id).finish(
                    "failed", ApiError(500, "TASK_INTERRUPTED", "执行器重启，任务已终止；请使用新请求重试。")
                )
            except asyncio.CancelledError:
                pass  # Deleted conversations are owned by the durable cleanup job.
        await leader.execute("SELECT pg_advisory_lock(743043::bigint)")
        await leader.execute("SELECT pg_advisory_unlock(743044::bigint)")
        log_event("worker_ready")
        while True:
            await leader.fetchval("SELECT 1", timeout=5)
            await files.tick(jobs)
            for task_id, job in tuple(jobs.items()):
                if job.done():
                    await asyncio.gather(job, return_exceptions=True)
                    jobs.pop(task_id)
            async with sessions() as db:
                cancelled = (
                    await db.scalars(
                        select(AnalysisTask.id).where(
                            AnalysisTask.id.in_(list(jobs)), AnalysisTask.cancel_requested_at.is_not(None)
                        )
                    )
                ).all()
            for task_id in cancelled:
                if not jobs[task_id].cancelling():
                    jobs[task_id].cancel()
            if len(jobs) < 3:
                candidate = await claim(sessions)
                if candidate:
                    task_id, timeout = candidate
                    jobs[task_id] = asyncio.create_task(execute(TaskContext(sessions, task_id), timeout, executor))
                    continue
            await asyncio.sleep(0.2)
    finally:
        await files.stop()
        for job in jobs.values():
            if not job.cancelling():
                job.cancel()
        await asyncio.gather(*jobs.values(), return_exceptions=True)
        await engine.dispose()
        await leader.close()


async def healthcheck():
    from app.attachments import resources_ready
    from app.core.database import Session, engine

    try:
        async with asyncio.timeout(2):
            async with Session() as db:
                await resources_ready(db)
        return True
    except Exception:
        return False
    finally:
        await engine.dispose()


if __name__ == "__main__":
    if sys.argv[1:] == ["--check"]:
        raise SystemExit(0 if asyncio.run(healthcheck()) else 1)
    asyncio.run(serve())
