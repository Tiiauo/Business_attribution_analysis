import asyncio
import json
import time
from datetime import datetime
from typing import Annotated, Literal

import asyncpg
from fastapi import APIRouter, Depends, Query, Request
from pydantic import AwareDatetime, Field
from sqlalchemy import and_, or_, select

from app.auth import DB, admin_user, current_user, now, validate_csrf
from app.core.audit import record_event
from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.security import redact
from app.model_client import chat_once
from app.models import DataSource, TaskLog, User
from app.runtime import (
    ModelConfig,
    SaveConfigRequest,
    StrictModel,
    VersionRequest,
    check_version,
    config_state,
    merge_config,
    resolve_credential,
    runtime_row,
    save_draft,
    store_credential,
    validate_bundle,
)

router = APIRouter(prefix="/api")
Admin = Annotated[User, Depends(admin_user)]
Current = Annotated[User, Depends(current_user)]


async def mutation(request: Request, user: Admin):
    validate_csrf(request)
    return user


Writer = Annotated[User, Depends(mutation)]


@router.get("/admin/configs")
async def configs(db: DB, user: Admin):
    return config_state(await runtime_row(db))


class ModelConnectionRequest(VersionRequest):
    model: ModelConfig
    api_key: str | None = Field(default=None, min_length=1, max_length=16000)


@router.post("/admin/model/test")
async def test_model(payload: ModelConnectionRequest, db: DB, user: Writer):
    row = await runtime_row(db)
    check_version(row, payload.expected_version)
    saved = (row.pending_value or row.config_value)["model"]
    secret = payload.api_key
    if secret is None:
        if not saved.get("credential_ref"):
            raise ApiError(400, "MODEL_NOT_CONFIGURED", "请填写 api_key。")
        secret = await resolve_credential(db, saved["credential_ref"])
    started = time.monotonic()
    try:
        result = await chat_once(
            payload.model.model_dump(),
            secret,
            [{"role": "user", "content": "Reply with OK only."}],
            session_id=f"ba-model-test-{user.id}",
        )
    except ApiError as error:
        record_event(
            db,
            "model_connection_tested",
            operator_id=user.id,
            target_type="model",
            target_id="runtime_bundle",
            level="WARN",
            version=payload.expected_version,
            status="failed",
            error_code=error.code,
        )
        await db.commit()
        raise
    record_event(
        db,
        "model_connection_tested",
        operator_id=user.id,
        target_type="model",
        target_id="runtime_bundle",
        version=payload.expected_version,
        status="success",
        usage=result.get("usage", {}),
    )
    await db.commit()
    return {
        "status": "success",
        "message": "模型连接成功，已收到真实响应；测试不会保存或发布配置。",
        "tested_version": payload.expected_version,
        "model": payload.model.model_id,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
        "usage": result.get("usage", {}),
    }


@router.post("/admin/configs")
async def save_configs(payload: SaveConfigRequest, db: DB, user: Writer):
    row = await runtime_row(db, lock=True)
    check_version(row, payload.expected_version)
    value = merge_config(row.pending_value or row.config_value, payload.changes)
    for group, secret in payload.credentials.items():
        if group not in {"auth", "model"}:
            raise ApiError(400, "CONFIG_INVALID", "凭证替换只接受 auth/model 分组。")
        if not isinstance(value.get(group), dict):
            raise ApiError(400, "CONFIG_INVALID", "凭证所在配置分组必须是对象。")
        value[group]["credential_ref"] = await store_credential(db, secret)
    value = await validate_bundle(db, value, row.config_value)
    save_draft(row, value)
    record_event(
        db,
        "config_saved",
        operator_id=user.id,
        target_type="config",
        target_id="runtime_bundle",
        version=row.pending_version,
    )
    await db.commit()
    return config_state(row)


async def source_check(db, identity: DataSource, config: dict) -> dict:
    checks = {
        key: False
        for key in ("connectivity", "target_scope", "read_only", "cross_schema_denied", "application_db_denied")
    }
    connection = None
    try:
        credential = json.loads(await resolve_credential(db, config["credential_ref"]))
        if set(credential) != {"username", "password"} or not all(
            isinstance(value, str) and value for value in credential.values()
        ):
            raise ValueError("Invalid credential")
        connection = await asyncpg.connect(
            host=identity.connection_target["host"],
            port=identity.connection_target["port"],
            database=identity.database_name,
            user=credential["username"],
            password=credential["password"],
            timeout=5,
            server_settings={"statement_timeout": "5000", "lock_timeout": "1000"},
        )
        checks["connectivity"] = True
        schema = identity.schema_name
        quoted = '"' + schema.replace('"', '""') + '"'
        tables = await connection.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname=$1 ORDER BY tablename", schema
        )
        checks["target_scope"] = (
            bool(tables) and await connection.fetchval("SELECT current_database()") == identity.database_name
        )
        if not tables:
            return {"status": "failed", "checks": checks, "message": "绑定 schema 不存在或没有业务表。"}
        table = quoted + '."' + tables[0]["tablename"].replace('"', '""') + '"'
        await connection.fetch(f"SELECT * FROM {table} LIMIT 1")
        unsafe_role = await connection.fetchval(
            "SELECT rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
        )
        can_create = await connection.fetchval(
            "SELECT has_database_privilege(current_database(),'CREATE') OR has_database_privilege(current_database(),'TEMP')"
        )
        can_write = await connection.fetchval("""SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' AND
            (has_schema_privilege(n.oid,'CREATE') OR (c.relkind IN ('r','p','v','m','f') AND
            (has_table_privilege(c.oid,'INSERT') OR has_table_privilege(c.oid,'UPDATE') OR has_table_privilege(c.oid,'DELETE') OR has_table_privilege(c.oid,'TRUNCATE')))))""")
        transaction = connection.transaction()
        await transaction.start()
        try:
            try:
                await connection.execute(f"DELETE FROM {table} WHERE false")
                denied_write = False
            except asyncpg.InsufficientPrivilegeError:
                denied_write = True
        finally:
            await transaction.rollback()
        membership = await connection.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname<>current_user AND pg_has_role(oid,'MEMBER'))"
        )
        checks["read_only"] = denied_write and not (unsafe_role or can_create or can_write or membership)
        checks["cross_schema_denied"] = True
        other_tables = await connection.fetch(
            """SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname <> $1 AND n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' AND c.relkind IN ('r','p','v','m','f')""",
            schema,
        )
        for item in other_tables:
            other = '"' + item["nspname"].replace('"', '""') + '"."' + item["relname"].replace('"', '""') + '"'
            try:
                await connection.fetch(f"SELECT * FROM {other} LIMIT 0")
                checks["cross_schema_denied"] = False
                break
            except asyncpg.InsufficientPrivilegeError:
                pass
        app_database = get_settings().database_url.get_secret_value()
        from sqlalchemy.engine import make_url

        app_name = make_url(app_database).database
        if identity.database_name != app_name:
            try:
                extra = await asyncpg.connect(
                    host=identity.connection_target["host"],
                    port=identity.connection_target["port"],
                    database=app_name,
                    user=credential["username"],
                    password=credential["password"],
                    timeout=5,
                )
            except asyncpg.InvalidCatalogNameError:
                checks["application_db_denied"] = True
            except asyncpg.InsufficientPrivilegeError:
                checks["application_db_denied"] = True
            else:
                await extra.close()
        good = all(checks.values())
        return {
            "status": "success" if good else "failed",
            "checks": checks,
            "message": "连通、范围、只读与隔离检查通过。" if good else "权限或范围检查未通过，请使用独立只读账号。",
        }
    except (asyncpg.PostgresError, OSError, ValueError, KeyError, TypeError, ApiError, TimeoutError):
        return {"status": "failed", "checks": checks, "message": "连接或凭证校验失败，请检查目标和只读授权。"}
    finally:
        if connection is not None:
            await connection.close()


@router.post("/admin/reload")
async def reload_configs(payload: VersionRequest, db: DB, user: Writer):
    row = await runtime_row(db, lock=True)
    check_version(row, payload.expected_version)
    if row.pending_value is None:
        return {
            "status": "no_change",
            "message": "当前无待发布变更。",
            "active_version": row.config_version,
            "pending_version": None,
        }
    try:
        value = await validate_bundle(db, row.pending_value, row.config_value)
        identities = {str(item.id): item for item in (await db.scalars(select(DataSource))).all()}
        for identifier, source in value["data_sources"].items():
            if source["status"] == "enabled":
                result = await asyncio.wait_for(source_check(db, identities[identifier], source), timeout=30)
                if result["status"] != "success":
                    raise ApiError(400, "CONFIG_INVALID", f"源 {identifier} 未通过只读及范围检查，草稿保留。")
    except (ApiError, TimeoutError) as error:
        record_event(
            db,
            "config_reload_failed",
            operator_id=user.id,
            target_type="config",
            target_id="runtime_bundle",
            level="WARN",
            version=row.pending_version,
        )
        await db.commit()
        if isinstance(error, ApiError):
            raise error
        raise ApiError(400, "CONFIG_INVALID", "数据源检查超时，当前有效配置及草稿保留。") from None
    row.config_value, row.config_version = value, row.pending_version
    row.pending_value = row.pending_version = None
    record_event(
        db,
        "config_reloaded",
        operator_id=user.id,
        target_type="config",
        target_id="runtime_bundle",
        version=row.config_version,
    )
    await db.commit()
    return {
        "status": "success",
        "message": "整份配置已生效。",
        "active_version": row.config_version,
        "pending_version": None,
    }


@router.get("/data-sources")
async def sources(db: DB, user: Current, view: Literal["effective", "admin"] = "effective"):
    if view == "admin" and user.role != "admin":
        raise ApiError(403, "FORBIDDEN", "此视图需要管理员权限。")
    row = await runtime_row(db)
    active = row.config_value["data_sources"]
    pending = (row.pending_value or row.config_value)["data_sources"]
    items = []
    for identity in (await db.scalars(select(DataSource).order_by(DataSource.id))).all():
        key = str(identity.id)
        effective, draft = active.get(key), pending.get(key)
        if view == "effective":
            if effective and effective["status"] == "enabled":
                items.append(
                    {
                        "id": key,
                        "name": effective["name"],
                        "status": "enabled",
                        "database": identity.database_name,
                        "schema": identity.schema_name,
                    }
                )
        elif effective or draft:
            items.append(
                {
                    "id": key,
                    "connection_target": identity.connection_target,
                    "database_name": identity.database_name,
                    "schema_name": identity.schema_name,
                    "effective": effective,
                    "pending": draft if row.pending_value else None,
                    "effective_status": effective["status"] if effective else "not_published",
                }
            )
    return {"items": items, "active_version": row.config_version, "pending_version": row.pending_version}


class Target(StrictModel):
    host: str = Field(min_length=1, max_length=253, pattern=r"^[a-zA-Z0-9.:-]+$")
    port: int = Field(ge=1, le=65535)


class SaveSourceRequest(VersionRequest):
    id: str | None = Field(default=None, pattern=r"^[1-9][0-9]*$")
    name: str = Field(min_length=1, max_length=200, pattern=r"\S")
    status: Literal["enabled", "disabled", "removed"]
    connection_target: Target | None = None
    database_name: str | None = Field(default=None, min_length=1, max_length=63)
    schema_name: str | None = Field(default=None, pattern=r"^[a-z_][a-z0-9_]{0,62}$")
    credential_ref: str | None = None
    username: str | None = Field(default=None, min_length=1, max_length=63)
    password: str | None = Field(default=None, min_length=1, max_length=16000)


@router.post("/admin/data-sources")
async def save_source(payload: SaveSourceRequest, db: DB, user: Writer):
    row = await runtime_row(db, lock=True)
    check_version(row, payload.expected_version)
    value = merge_config(row.pending_value or row.config_value, {})
    identity = await db.get(DataSource, int(payload.id)) if payload.id else None
    if payload.id and identity is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "数据源不存在。")
    if identity:
        for field in ("connection_target", "database_name", "schema_name"):
            supplied = getattr(payload, field)
            supplied = supplied.model_dump() if isinstance(supplied, Target) else supplied
            if supplied is not None and supplied != getattr(identity, field):
                raise ApiError(409, "DATA_SOURCE_SCOPE_LOCKED", "连接目标与业务范围不可修改，请新建数据源。")
    else:
        if not (payload.connection_target and payload.database_name and payload.schema_name):
            raise ApiError(400, "INVALID_REQUEST", "新建数据源须填写连接目标、数据库及 schema。")
        identity = DataSource(
            connection_target=payload.connection_target.model_dump(),
            database_name=payload.database_name,
            schema_name=payload.schema_name,
        )
        db.add(identity)
        await db.flush()
    key = str(identity.id)
    reference = payload.credential_ref or value["data_sources"].get(key, {}).get("credential_ref")
    if payload.username is not None or payload.password is not None:
        if not (payload.username and payload.password):
            raise ApiError(400, "INVALID_REQUEST", "替换数据库凭证时须同时填写账号和密码。")
        reference = await store_credential(db, json.dumps({"username": payload.username, "password": payload.password}))
    if not reference or reference == "oidc:bootstrap":
        raise ApiError(400, "CREDENTIAL_UNAVAILABLE", "请设置数据源专用凭证。")
    value["data_sources"][key] = {"name": payload.name, "credential_ref": reference, "status": payload.status}
    save_draft(row, await validate_bundle(db, value, row.config_value))
    record_event(
        db,
        "data_source_saved",
        operator_id=user.id,
        target_type="data_source",
        target_id=key,
        version=row.pending_version,
    )
    await db.commit()
    return {"id": key, "pending_version": row.pending_version}


@router.delete("/admin/data-sources/{data_source_id}")
async def remove_source(data_source_id: int, payload: VersionRequest, db: DB, user: Writer):
    row = await runtime_row(db, lock=True)
    check_version(row, payload.expected_version)
    value = merge_config(row.pending_value or row.config_value, {})
    key = str(data_source_id)
    if key not in value["data_sources"]:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "数据源不存在。")
    value["data_sources"][key]["status"] = "removed"
    save_draft(row, value)
    record_event(
        db,
        "data_source_removed",
        operator_id=user.id,
        target_type="data_source",
        target_id=key,
        version=row.pending_version,
    )
    await db.commit()
    return {"id": key, "pending_version": row.pending_version}


@router.post("/admin/data-sources/{data_source_id}/test")
async def test_source(data_source_id: int, db: DB, user: Writer):
    row = await runtime_row(db)
    version = row.pending_version or row.config_version
    config = (row.pending_value or row.config_value)["data_sources"].get(str(data_source_id))
    identity = await db.get(DataSource, data_source_id)
    if identity is None or config is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "数据源不存在。")
    try:
        result = await asyncio.wait_for(source_check(db, identity, config), timeout=30)
    except TimeoutError:
        result = {"status": "failed", "checks": {}, "message": "连接检查超时。"}
    record_event(
        db,
        "data_source_tested",
        operator_id=user.id,
        target_type="data_source",
        target_id=str(identity.id),
        level="INFO" if result["status"] == "success" else "WARN",
        tested_version=version,
        result=result["status"],
        checks=result["checks"],
    )
    await db.commit()
    return {**result, "tested_version": version}


@router.get("/admin/logs")
async def logs(
    db: DB,
    user: Admin,
    scope: Literal["all", "task", "system"] = "all",
    level: Literal["DEBUG", "INFO", "WARN", "ERROR"] | None = None,
    task_id: int | None = None,
    operator_id: int | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    start_time: AwareDatetime | None = None,
    end_time: AwareDatetime | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    filters = []
    if scope != "all":
        filters.append(TaskLog.task_id.is_(None) if scope == "system" else TaskLog.task_id.is_not(None))
    for field, value in (
        (TaskLog.log_level, level),
        (TaskLog.task_id, task_id),
        (TaskLog.operator_id, operator_id),
        (TaskLog.target_type, target_type),
        (TaskLog.target_id, target_id),
    ):
        if value is not None:
            filters.append(field == value)
    if start_time and end_time and start_time > end_time:
        raise ApiError(400, "INVALID_REQUEST", "开始时间不得晚于结束时间。")
    if start_time:
        filters.append(TaskLog.created_at >= start_time)
    if end_time:
        filters.append(TaskLog.created_at <= end_time)
    if cursor:
        try:
            timestamp, identifier = cursor.rsplit("|", 1)
            timestamp = datetime.fromisoformat(timestamp)
            if timestamp.tzinfo is None or int(identifier) < 1:
                raise ValueError()
            filters.append(
                or_(TaskLog.created_at < timestamp, and_(TaskLog.created_at == timestamp, TaskLog.id < int(identifier)))
            )
        except (ValueError, OverflowError):
            raise ApiError(400, "INVALID_CURSOR", "日志游标无效，请重新查询。") from None
    rows = (
        await db.scalars(
            select(TaskLog).where(*filters).order_by(TaskLog.created_at.desc(), TaskLog.id.desc()).limit(limit + 1)
        )
    ).all()
    page = rows[:limit]
    items = [
        {
            "id": str(item.id),
            "created_at": item.created_at,
            "log_level": item.log_level,
            "log_type": item.log_type,
            "task_id": str(item.task_id) if item.task_id else None,
            "operator_id": str(item.operator_id) if item.operator_id else None,
            "target_type": item.target_type,
            "target_id": item.target_id,
            "details": redact(json.loads(item.log_content)),
        }
        for item in page
    ]
    return {
        "items": items,
        "next_cursor": f"{page[-1].created_at.isoformat()}|{page[-1].id}" if len(rows) > limit else None,
        "checked_at": now(),
    }
