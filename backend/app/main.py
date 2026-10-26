from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from app.admin import router as admin_router
from app.attachments import router as attachment_router
from app.auth import router
from app.chat import router as chat_router
from app.core.config import get_settings
from app.core.database import Session, engine
from app.core.errors import ApiError, install_errors
from app.core.security import cipher, safe_path
from app.reports import router as report_router
from app.runtime import initialize_runtime
from app.task_events import Notifications
from app.tasks import router as task_router


@asynccontextmanager
async def lifespan(app):
    settings = get_settings()
    cipher(settings.master_key_file)
    _ = settings.client_secret
    for directory in ("uploads", "exports", "workspace"):
        safe_path(settings.data_root, directory).mkdir(parents=True, exist_ok=True)
    async with Session() as db:
        await initialize_runtime(db)
    app.state.notifications = Notifications()
    await app.state.notifications.start()
    try:
        yield
    finally:
        await app.state.notifications.stop()
        await engine.dispose()


app = FastAPI(
    title="经营归因分析系统", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
)
install_errors(app)
app.include_router(router)
app.include_router(admin_router)
app.include_router(chat_router)
app.include_router(task_router)
app.include_router(attachment_router)
app.include_router(report_router)


@app.get("/health/live", include_in_schema=False)
async def live():
    return {"status": "ok"}


@app.get("/health/ready", include_in_schema=False)
async def ready():
    try:
        async with Session() as db:
            revision = await db.scalar(text("SELECT version_num FROM alembic_version"))
        if revision != "0003_m02":
            raise ValueError("Migration required")
    except Exception:
        raise ApiError(503, "DEPENDENCY_UNAVAILABLE", "数据库或迁移尚未就绪。") from None
    return {"status": "ready"}
