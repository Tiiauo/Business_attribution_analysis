"""One persisted configuration bundle; credentials are immutable encrypted references."""

from copy import deepcopy
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.errors import ApiError
from app.core.security import cipher
from app.models import DataSource, SystemConfig


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class AuthConfig(StrictModel):
    issuer_url: str
    issuer_internal_url: str
    client_id: str = Field(min_length=1, max_length=200)
    callback_url: str
    credential_ref: str
    role_claim: str = Field(min_length=1, max_length=100)
    role_mapping: dict[str, Literal["analyst", "admin"]] = Field(min_length=1)


class ModelConfig(StrictModel):
    api_url: str = ""
    model_id: str = Field(default="", max_length=200)
    credential_ref: str | None = None
    timeout_seconds: int = Field(default=120, ge=1, le=600)


class ToolsConfig(StrictModel):
    sql_enabled: bool = True
    file_enabled: bool = True
    search_enabled: bool = True
    command_enabled: bool = False
    allowed_commands: list[Literal["python_stats", "text_search"]] = []
    sql_timeout_seconds: int = Field(default=30, ge=1, le=600)
    command_timeout_seconds: int = Field(default=60, ge=1, le=600)
    task_timeout_seconds: int = Field(default=600, ge=1, le=3600)
    cleanup_step_timeout_seconds: int = Field(default=60, ge=1, le=600)


class ContextConfig(StrictModel):
    context_window_tokens: int | None = Field(default=None, ge=1)
    response_reserved_tokens: int = Field(default=4096, ge=1)
    summary_trigger_ratio: float = Field(default=0.8, gt=0, lt=1)
    summary_max_tokens: int = Field(default=2048, ge=1)
    recent_message_count: int = Field(default=10, ge=1, le=1000)

    @model_validator(mode="after")
    def budget(self):
        if self.context_window_tokens is not None:
            if self.context_window_tokens <= self.response_reserved_tokens + self.summary_max_tokens:
                raise ValueError("模型窗口必须大于响应与摘要保留量之和")
            if (
                int((self.context_window_tokens - self.response_reserved_tokens) * self.summary_trigger_ratio)
                <= self.summary_max_tokens
            ):
                raise ValueError("摘要触发预算必须能容纳摘要")
        return self


class ParseLimits(StrictModel):
    # These ceilings match the initial deployment; raising them requires an isolation capacity review.
    parse_timeout_seconds: int = Field(default=60, ge=1, le=60)
    parse_max_memory_bytes: int = Field(default=268435456, ge=1048576, le=268435456)
    xlsx_max_uncompressed_bytes: int = Field(default=200000000, ge=1, le=200000000)
    parse_max_rows: int = Field(default=100000, ge=1, le=100000)
    text_max_chars: int = Field(default=2000000, ge=1, le=2000000)


class SourceConfig(StrictModel):
    name: str = Field(min_length=1, max_length=200, pattern=r"\S")
    credential_ref: str
    status: Literal["enabled", "disabled", "removed"]


class RuntimeBundle(StrictModel):
    auth: AuthConfig
    model: ModelConfig = ModelConfig()
    tools: ToolsConfig = ToolsConfig()
    context: ContextConfig = ContextConfig()
    parse_limits: ParseLimits = ParseLimits()
    data_sources: dict[str, SourceConfig] = {}

    @model_validator(mode="after")
    def urls_and_model(self):
        for index, value in enumerate(
            (self.auth.issuer_url, self.auth.issuer_internal_url, self.auth.callback_url, self.model.api_url)
        ):
            if not value:
                continue
            parsed = urlsplit(value)
            if (
                (
                    parsed.scheme != "https"
                    and not (
                        index == 1
                        and parsed.scheme == "http"
                        and value == self.auth.issuer_internal_url == get_settings().issuer_internal_url
                    )
                )
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("服务地址须使用 HTTPS，不得含凭证、查询或片段")
        if self.auth.callback_url != get_settings().callback_url:
            raise ValueError("回调地址必须与当前部署的 /auth/callback 一致")
        if self.model.credential_ref and not (
            self.model.api_url and self.model.model_id and self.context.context_window_tokens
        ):
            raise ValueError("配置模型凭证时必须填写地址、模型标识与实际上下文窗口")
        return self


class ConfigState(BaseModel):
    active_version: int
    pending_version: int | None
    edit_version: int
    effective_config: RuntimeBundle
    pending_config: dict | None


class VersionRequest(StrictModel):
    expected_version: int = Field(ge=1)


class SaveConfigRequest(VersionRequest):
    changes: dict
    credentials: dict[str, str] = {}


async def initialize_runtime(db: AsyncSession):
    settings = get_settings()
    bundle = RuntimeBundle(
        auth=AuthConfig(
            issuer_url=settings.issuer_url,
            issuer_internal_url=settings.issuer_internal_url,
            client_id=settings.client_id,
            callback_url=settings.callback_url,
            credential_ref="oidc:bootstrap",
            role_claim=settings.role_claim,
            role_mapping=settings.role_mapping,
        )
    )
    await db.execute(
        insert(SystemConfig)
        .values(
            config_group="runtime",
            config_key="runtime_bundle",
            config_value=bundle.model_dump(),
            config_version=1,
        )
        .on_conflict_do_nothing(index_elements=["config_group", "config_key"])
    )
    await db.commit()


async def runtime_row(db: AsyncSession, lock=False) -> SystemConfig:
    query = select(SystemConfig).where(
        SystemConfig.config_group == "runtime", SystemConfig.config_key == "runtime_bundle"
    )
    row = await db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise ApiError(503, "CONFIG_NOT_READY", "运行配置尚未初始化。")
    return row


async def published_runtime(db: Annotated[AsyncSession, Depends(get_db)]) -> RuntimeBundle:
    return RuntimeBundle.model_validate((await runtime_row(db)).config_value)


async def execution_config(db: AsyncSession, data_source_id: str) -> tuple[int, dict]:
    """M04/M05 call this at execution start, then retain the returned value."""
    row = await runtime_row(db)
    value = deepcopy(row.config_value)
    source = value["data_sources"].get(data_source_id)
    if not source or source["status"] != "enabled":
        raise ApiError(409, "DATA_SOURCE_UNAVAILABLE", "数据源未发布或已停用，请新建会话选择可用源。")
    if not (
        value["model"]["credential_ref"]
        and value["model"]["api_url"]
        and value["model"]["model_id"]
        and value["context"]["context_window_tokens"]
    ):
        raise ApiError(409, "MODEL_NOT_CONFIGURED", "模型凭证或实际窗口未配置，不能开始分析。")
    identity = await db.get(DataSource, int(data_source_id))
    if identity is None:
        raise ApiError(409, "DATA_SOURCE_UNAVAILABLE", "数据源身份不可用。")
    value["data_sources"] = {
        data_source_id: {
            **source,
            "connection_target": identity.connection_target,
            "database_name": identity.database_name,
            "schema_name": identity.schema_name,
        }
    }
    return row.config_version, value


def config_state(row: SystemConfig) -> ConfigState:
    return ConfigState(
        active_version=row.config_version,
        pending_version=row.pending_version,
        edit_version=row.pending_version or row.config_version,
        effective_config=row.config_value,
        pending_config=row.pending_value,
    )


def check_version(row: SystemConfig, expected: int):
    if expected != (row.pending_version or row.config_version):
        raise ApiError(409, "CONFIG_VERSION_CONFLICT", "配置版本已变化。请读取最新版本并核对本地编辑后再保存。")


def merge_config(base: dict, changes: dict) -> dict:
    result = deepcopy(base)
    for key, value in changes.items():
        if key != "role_mapping" and isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_config(result[key], value)
        else:
            result[key] = value
    return result


async def store_credential(db: AsyncSession, value: str) -> str:
    if not value or len(value) > 16000:
        raise ApiError(400, "CONFIG_INVALID", "新凭证不能为空或超过 16000 字符。")
    reference = "secret:" + uuid4().hex
    db.add(
        SystemConfig(
            config_group="credentials",
            config_key=reference,
            config_version=1,
            config_value={"encrypted": cipher(get_settings().master_key_file).encrypt(value.encode()).decode()},
        )
    )
    await db.flush()
    return reference


async def resolve_credential(db: AsyncSession, reference: str) -> str:
    if reference == "oidc:bootstrap":
        return get_settings().client_secret
    row = await db.scalar(
        select(SystemConfig).where(SystemConfig.config_group == "credentials", SystemConfig.config_key == reference)
    )
    if row is None:
        raise ApiError(400, "CREDENTIAL_UNAVAILABLE", "凭证引用不存在，请重新设置凭证。")
    return cipher(get_settings().master_key_file).decrypt(row.config_value["encrypted"].encode()).decode()


async def validate_bundle(db: AsyncSession, value: dict, active: dict) -> dict:
    try:
        bundle = RuntimeBundle.model_validate(value)
    except (ValidationError, ValueError) as error:
        fields = (
            ", ".join(".".join(map(str, entry["loc"])) for entry in error.errors())
            if isinstance(error, ValidationError)
            else "runtime"
        )
        raise ApiError(400, "CONFIG_INVALID", f"配置整体校验失败，请检查字段：{fields}。") from None
    identities = set(map(str, (await db.scalars(select(DataSource.id))).all()))
    if (
        not set(active.get("data_sources", {})) <= set(bundle.data_sources)
        or not set(bundle.data_sources) <= identities
    ):
        raise ApiError(400, "CONFIG_INVALID", "数据源必须先登记身份，已发布源须保留条目并以 removed 移除。")
    refs = [bundle.auth.credential_ref, bundle.model.credential_ref] + [
        item.credential_ref for item in bundle.data_sources.values()
    ]
    for reference in filter(None, refs):
        await resolve_credential(db, reference)
    return bundle.model_dump()


def save_draft(row: SystemConfig, value: dict):
    row.pending_version = (row.pending_version or row.config_version) + 1
    row.pending_value = value
