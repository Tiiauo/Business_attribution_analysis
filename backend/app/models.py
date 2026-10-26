from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Identity:
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)


class Created:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Updated(Created):
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class User(Identity, Updated, Base):
    __tablename__ = "users"
    external_user_id: Mapped[str] = mapped_column(Text, unique=True)
    username: Mapped[str] = mapped_column(String(200))
    display_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), server_default="active")
    __table_args__ = (
        CheckConstraint("role IN ('analyst','admin')"),
        CheckConstraint("status IN ('active','disabled')"),
    )


class DataSource(Identity, Created, Base):
    __tablename__ = "data_sources"
    connection_target: Mapped[dict] = mapped_column(JSONB)
    database_name: Mapped[str] = mapped_column(String(200))
    schema_name: Mapped[str] = mapped_column(String(200))


class Conversation(Identity, Updated, Base):
    __tablename__ = "conversations"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(100), server_default="新会话")
    status: Mapped[str] = mapped_column(String(20), server_default="active")
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    title_source: Mapped[str] = mapped_column(String(20), server_default="default")
    data_source_id: Mapped[int | None] = mapped_column(ForeignKey("data_sources.id", ondelete="RESTRICT"))
    cleanup_status: Mapped[str] = mapped_column(String(30), server_default="none")
    cleanup_error: Mapped[str | None] = mapped_column(Text)
    cleanup_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("status IN ('active','archived','deleted')"),
        CheckConstraint("title_source IN ('default','auto','manual')"),
        CheckConstraint("cleanup_status IN ('none','queued','stopping','deleting_files','deleting_records','failed')"),
    )


class Message(Identity, Updated, Base):
    __tablename__ = "messages"
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey(
            "analysis_tasks.id",
            ondelete="CASCADE",
            use_alter=True,
            name="fk_messages_task",
            deferrable=True,
            initially="DEFERRED",
        )
    )
    role: Mapped[str] = mapped_column(String(20))
    message_type: Mapped[str] = mapped_column(String(30))
    content: Mapped[str] = mapped_column(Text, server_default="")
    tool_name: Mapped[str | None] = mapped_column(String(200))
    tool_status: Mapped[str | None] = mapped_column(String(20))
    tool_call_id: Mapped[str | None] = mapped_column(String(200))
    seq_no: Mapped[int] = mapped_column(BigInteger)
    message_status: Mapped[str] = mapped_column(String(20), server_default="complete")
    tool_input_json: Mapped[dict | None] = mapped_column(JSONB)
    tool_outputs_json: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    __table_args__ = (
        UniqueConstraint("conversation_id", "seq_no"),
        UniqueConstraint("task_id", "tool_call_id"),
        CheckConstraint("seq_no > 0"),
        CheckConstraint("role IN ('user','assistant','tool')"),
        CheckConstraint("message_type IN ('user_input','assistant_text','tool_call')"),
        CheckConstraint("message_status IN ('streaming','complete','failed','cancelled')"),
        CheckConstraint("tool_status IS NULL OR tool_status IN ('running','success','failed','cancelled')"),
    )


class Attachment(Identity, Updated, Base):
    __tablename__ = "attachments"
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    file_name: Mapped[str] = mapped_column(Text)
    file_path: Mapped[str] = mapped_column(Text)
    file_type: Mapped[str] = mapped_column(String(20))
    file_size: Mapped[int] = mapped_column(BigInteger)
    parse_status: Mapped[str] = mapped_column(String(20), server_default="pending")
    parse_error: Mapped[str | None] = mapped_column(Text)
    delete_status: Mapped[str] = mapped_column(String(20), server_default="none")
    delete_error: Mapped[str | None] = mapped_column(Text)
    parse_options_json: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    __table_args__ = (
        CheckConstraint("file_size BETWEEN 0 AND 20000000"),
        CheckConstraint("parse_status IN ('pending','parsing','success','failed')"),
        CheckConstraint("delete_status IN ('none','deleting','failed')"),
    )


class AnalysisTask(Identity, Created, Base):
    __tablename__ = "analysis_tasks"
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE", deferrable=True, initially="DEFERRED"), unique=True
    )
    client_request_id: Mapped[str] = mapped_column(String(128))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    input_text: Mapped[str] = mapped_column(Text)
    task_status: Mapped[str] = mapped_column(String(20), server_default="queued")
    current_step: Mapped[str] = mapped_column(String(200), server_default="等待执行")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)
    last_event_id: Mapped[int] = mapped_column(BigInteger, server_default="0")
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    task_kind: Mapped[str] = mapped_column(String(20), server_default="analysis")
    attachment_snapshot_json: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    config_version: Mapped[int | None] = mapped_column(Integer)
    config_snapshot_json: Mapped[dict | None] = mapped_column(JSONB)
    final_report_scope_json: Mapped[dict | None] = mapped_column(JSONB)
    __table_args__ = (
        UniqueConstraint("conversation_id", "client_request_id"),
        CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$'"),
        CheckConstraint("task_status IN ('queued','running','success','failed','cancelled')"),
        CheckConstraint("task_kind IN ('analysis','final_report')"),
        CheckConstraint("last_event_id >= 0"),
        Index(
            "uq_conversation_unfinished_task",
            "conversation_id",
            unique=True,
            postgresql_where=text("task_status IN ('queued','running')"),
        ),
    )


class AnalysisResult(Identity, Created, Base):
    __tablename__ = "analysis_results"
    task_id: Mapped[int] = mapped_column(ForeignKey("analysis_tasks.id", ondelete="CASCADE"), unique=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    problem_definition: Mapped[str] = mapped_column(Text)
    key_metrics_json: Mapped[list] = mapped_column(JSONB)
    evidence_list_json: Mapped[list] = mapped_column(JSONB)
    conclusion_text: Mapped[str] = mapped_column(Text)
    missing_data_text: Mapped[str] = mapped_column(Text)
    next_action_text: Mapped[str] = mapped_column(Text)
    result_markdown: Mapped[str] = mapped_column(Text)
    result_file_path: Mapped[str | None] = mapped_column(Text)
    export_status_json: Mapped[dict] = mapped_column(
        JSONB,
        server_default=text(
            '\'{"markdown":{"status":"not_generated","error_message": null,"generated_at": null},'
            '"html":{"status":"not_generated","error_message": null,"generated_at": null}}\'::jsonb'
        ),
    )


class ContextSummary(Identity, Created, Base):
    __tablename__ = "context_summaries"
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    start_seq_no: Mapped[int] = mapped_column(BigInteger)
    end_seq_no: Mapped[int] = mapped_column(BigInteger)
    summary_text: Mapped[str] = mapped_column(Text)
    __table_args__ = (CheckConstraint("start_seq_no > 0 AND end_seq_no >= start_seq_no"),)


class WebsocketToken(Identity, Created, Base):
    __tablename__ = "websocket_tokens"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    token: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SystemConfig(Identity, Base):
    __tablename__ = "system_configs"
    config_key: Mapped[str] = mapped_column(String(100))
    config_group: Mapped[str] = mapped_column(String(100))
    config_value: Mapped[dict] = mapped_column(JSONB)
    config_version: Mapped[int] = mapped_column(Integer, server_default="1")
    pending_value: Mapped[dict | None] = mapped_column(JSONB)
    pending_version: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    __table_args__ = (
        UniqueConstraint("config_group", "config_key"),
        CheckConstraint("config_version > 0"),
        CheckConstraint(
            "(pending_value IS NULL AND pending_version IS NULL) OR "
            "(pending_value IS NOT NULL AND pending_version > config_version)"
        ),
    )


class TaskLog(Identity, Created, Base):
    __tablename__ = "task_logs"
    task_id: Mapped[int | None] = mapped_column(ForeignKey("analysis_tasks.id", ondelete="CASCADE"), index=True)
    operator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    target_type: Mapped[str] = mapped_column(String(100))
    target_id: Mapped[str] = mapped_column(String(200))
    log_level: Mapped[str] = mapped_column(String(20))
    log_type: Mapped[str] = mapped_column(String(100))
    log_content: Mapped[str] = mapped_column(Text)
    __table_args__ = (CheckConstraint("log_level IN ('DEBUG','INFO','WARN','ERROR')"),)


class LoginAttempt(Identity, Created, Base):
    __tablename__ = "login_attempts"
    state_hash: Mapped[str] = mapped_column(String(64), unique=True)
    browser_hash: Mapped[str] = mapped_column(String(64))
    nonce: Mapped[str] = mapped_column(String(100))
    verifier_encrypted: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    auth_config: Mapped[dict | None] = mapped_column(JSONB)


class UserSession(Identity, Created, Base):
    __tablename__ = "user_sessions"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DemoIdentity(Identity, Created, Base):
    __tablename__ = "demo_identities"
    username: Mapped[str] = mapped_column(String(100), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(20))
    password_hash: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), server_default="active")
    __table_args__ = (
        CheckConstraint("role IN ('analyst','admin')"),
        CheckConstraint("status IN ('active','disabled')"),
    )


class AuthorizationInteraction(Identity, Created, Base):
    __tablename__ = "demo_authorization_interactions"
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    parameters: Mapped[dict] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_attempts: Mapped[int] = mapped_column(Integer, server_default="0")


class AuthorizationCode(Identity, Created, Base):
    __tablename__ = "demo_authorization_codes"
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    identity_id: Mapped[int] = mapped_column(ForeignKey("demo_identities.id", ondelete="CASCADE"))
    parameters: Mapped[dict] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DemoAccessToken(Identity, Created, Base):
    __tablename__ = "demo_access_tokens"
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    identity_id: Mapped[int] = mapped_column(ForeignKey("demo_identities.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
