import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import redact
from app.models import TaskLog


def record_event(
    db: AsyncSession,
    log_type: str,
    *,
    operator_id: int | None,
    target_type: str,
    target_id: str,
    task_id: int | None = None,
    level: str = "INFO",
    **details,
) -> None:
    db.add(
        TaskLog(
            task_id=task_id,
            operator_id=operator_id,
            target_type=target_type,
            target_id=target_id,
            log_level=level,
            log_type=log_type,
            log_content=json.dumps(redact(details), ensure_ascii=False, default=str),
        )
    )
