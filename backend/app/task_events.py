import asyncio
import json

import asyncpg
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.auth import now
from app.contracts import TaskEvent
from app.core.config import get_settings
from app.core.security import log_event

CHANNEL = "ba_task_events"


def database_dsn():
    return (
        make_url(get_settings().database_url.get_secret_value())
        .set(drivername="postgresql")
        .render_as_string(hide_password=False)
    )


async def event(db, task, kind: str, **fields):
    task.last_event_id += 1
    value = {
        "type": kind,
        "conversation_id": str(task.conversation_id),
        "task_id": str(task.id),
        "event_id": task.last_event_id,
        "created_at": now(),
        **fields,
    }
    value = jsonable_encoder(TaskEvent.model_validate(value).root)
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(payload.encode("utf-8")) >= 7900:
        raise ValueError("Task event exceeds notification size")
    # PostgreSQL delivers NOTIFY only after the enclosing state transaction commits.
    await db.execute(text("SELECT pg_notify(:channel,:payload)"), {"channel": CHANNEL, "payload": payload})


async def status_event(db, task):
    await event(
        db,
        task,
        "task_status",
        task_status=task.task_status,
        current_step=task.current_step,
        cancel_requested_at=task.cancel_requested_at,
        is_cancelling=task.cancel_requested_at is not None and task.task_status in {"queued", "running"},
    )


class Notifications:
    def __init__(self):
        self.subscribers = {}
        self.connection = None
        self.job = None

    def received(self, connection, pid, channel, payload):
        try:
            value = TaskEvent.model_validate_json(payload).root.model_dump(mode="json")
        except ValueError:
            log_event("invalid_task_notification")
            return
        for queue in tuple(self.subscribers.get(value["conversation_id"], ())):
            try:
                queue.put_nowait(value)
            except asyncio.QueueFull:
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(None)

    async def connect(self):
        self.connection = await asyncpg.connect(database_dsn(), timeout=5)
        await self.connection.add_listener(CHANNEL, self.received)

    async def start(self):
        await self.connect()
        self.job = asyncio.create_task(self.watch())

    async def watch(self):
        while True:
            await asyncio.sleep(2)
            try:
                if self.connection is None or self.connection.is_closed():
                    await self.connect()
                await self.connection.fetchval("SELECT 1", timeout=3)
            except (asyncpg.PostgresError, OSError, TimeoutError):
                if self.connection is not None:
                    self.connection.terminate()
                self.connection = None
                log_event("task_notification_reconnect")

    async def stop(self):
        if self.job:
            self.job.cancel()
            await asyncio.gather(self.job, return_exceptions=True)
        if self.connection and not self.connection.is_closed():
            await self.connection.close(timeout=3)

    def subscribe(self, conversation_id):
        queue = asyncio.Queue(maxsize=128)
        self.subscribers.setdefault(conversation_id, set()).add(queue)
        return queue

    def unsubscribe(self, conversation_id, queue):
        group = self.subscribers.get(conversation_id)
        if group:
            group.discard(queue)
            if not group:
                self.subscribers.pop(conversation_id, None)
