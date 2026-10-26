from typing import Annotated, Any, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    field_validator,
    model_serializer,
    model_validator,
)

Role = Literal["analyst", "admin"]
UserStatus = Literal["active", "disabled"]
TaskStatus = Literal["queued", "running", "success", "failed", "cancelled"]
TaskKind = Literal["analysis", "final_report"]
MessageStatus = Literal["streaming", "complete", "failed", "cancelled"]
ExportStatus = Literal["not_generated", "generating", "success", "failed"]
ConversationStatus = Literal["active", "archived", "deleted"]
ParseStatus = Literal["pending", "parsing", "success", "failed"]
AttachmentDeleteStatus = Literal["none", "deleting", "failed"]
CleanupStatus = Literal["none", "queued", "stopping", "deleting_files", "deleting_records", "failed"]
UnavailableReason = Literal["deleting", "delete_failed", "deleted", "storage_missing", "attachment_deleted"]
PositiveID = Annotated[str, Field(min_length=1, strict=True)]


def validate_relative_path(value: str) -> str:
    if value.startswith(("/", "\\")) or ":" in value or "\x00" in value:
        raise ValueError("Output path must be relative")
    if any(part in {"", ".", ".."} for part in value.replace("\\", "/").split("/")):
        raise ValueError("Output path must not contain traversal")
    return value


class UserInfo(BaseModel):
    id: str
    external_user_id: str
    username: str
    display_name: str
    role: Role
    status: UserStatus
    csrf_token: str
    expires_at: AwareDatetime


class ErrorResponse(BaseModel):
    code: str
    message: str
    request_id: str


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None


class SourceLocator(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True, serialize_by_alias=True)
    source_task_id: PositiveID | None = None
    source_evidence_id: PositiveID | None = None
    available_at_result: bool | None = None
    unavailable_reason_at_result: UnavailableReason | None = None
    available: bool | None = None
    unavailable_reason: UnavailableReason | None = None
    availability_checked_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def paired_reference(self):
        if (self.source_task_id is None) != (self.source_evidence_id is None):
            raise ValueError("Historical evidence requires both task and evidence identifiers")
        return self


class DatabaseLocator(SourceLocator):
    data_source_id: str = Field(min_length=1)
    database: str = Field(min_length=1)
    schema_name: str = Field(alias="schema", min_length=1)
    query_text: str = Field(min_length=1)
    filter_summary: str = Field(min_length=1)
    extracted_at: AwareDatetime


class FileLocator(SourceLocator):
    attachment_id: str = Field(min_length=1)
    file_name: str = Field(min_length=1)
    position: str | dict[str, Any] = Field(min_length=1)


class ToolLocator(SourceLocator):
    task_id: str = Field(min_length=1)
    tool_call_id: str = Field(min_length=1)
    output_id: str = Field(min_length=1)
    output_kind: Literal["stdout", "file"]
    relative_path: str = Field(min_length=1)
    position: str | dict[str, Any] = Field(min_length=1)
    generated_at: AwareDatetime

    @model_validator(mode="after")
    def bounded_path(self):
        validate_relative_path(self.relative_path)
        return self


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_id: str = Field(min_length=1)
    source_type: Literal["database", "file", "tool_output"]
    source_name: str = Field(min_length=1)
    evidence_text: str = Field(min_length=1, description="逐字复制已登记工具证据摘录，不改写数据或追加解释")
    related_metric: str = Field(
        min_length=1, description="说明支撑的问题/指标和置信依据；解释放在这里或结论，不能增加第八个字段"
    )
    confidence: Literal["high", "medium", "low"]
    source_locator: DatabaseLocator | FileLocator | ToolLocator

    @model_validator(mode="after")
    def correct_locator(self):
        expected = {"database": DatabaseLocator, "file": FileLocator, "tool_output": ToolLocator}
        if not isinstance(self.source_locator, expected[self.source_type]):
            raise ValueError("Evidence source type does not match its locator")
        return self


class SubscriptionReady(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["subscription_ready"]
    conversation_id: PositiveID
    created_at: AwareDatetime


class EventBase(BaseModel):
    model_config = ConfigDict(extra="allow")
    conversation_id: PositiveID
    task_id: PositiveID
    event_id: int = Field(ge=1)
    created_at: AwareDatetime


class MessageStart(EventBase):
    type: Literal["message_start"]
    message_id: PositiveID
    seq_no: int = Field(ge=1)
    user_message_id: PositiveID
    message_status: Literal["streaming"]


class MessageDelta(EventBase):
    type: Literal["message_delta"]
    message_id: PositiveID
    seq_no: int = Field(ge=1)
    delta_text: str


class ToolStart(EventBase):
    type: Literal["tool_start"]
    message_id: PositiveID
    seq_no: int = Field(ge=1)
    tool_name: str
    tool_call_id: PositiveID
    tool_status: Literal["running"]


class ToolFinish(EventBase):
    type: Literal["tool_finish"]
    message_id: PositiveID
    seq_no: int = Field(ge=1)
    tool_name: str
    tool_call_id: PositiveID
    tool_status: Literal["success", "failed", "cancelled"]
    tool_result_summary: str


class TaskStatusEvent(EventBase):
    type: Literal["task_status"]
    task_status: TaskStatus
    current_step: str = Field(max_length=200)
    cancel_requested_at: AwareDatetime | None
    is_cancelling: bool


class ResultReady(EventBase):
    type: Literal["result_ready"]
    result_id: PositiveID


class ErrorEvent(EventBase):
    type: Literal["error"]
    error_message: str
    code: str


class Done(EventBase):
    type: Literal["done"]
    finished_at: AwareDatetime


class ExportEvent(EventBase):
    type: Literal["export_status"]
    format: Literal["markdown", "html"]
    status: ExportStatus
    error_message: str | None


class TaskEvent(
    RootModel[
        Annotated[
            MessageStart
            | MessageDelta
            | ToolStart
            | ToolFinish
            | TaskStatusEvent
            | ResultReady
            | ErrorEvent
            | Done
            | ExportEvent,
            Field(discriminator="type"),
        ]
    ]
):
    pass


class ParseOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    has_header: bool | None = None

    @model_serializer
    def serialize(self):
        return {} if self.has_header is None else {"has_header": self.has_header}


class AttachmentSnapshot(BaseModel):
    attachment_id: PositiveID
    file_name: str
    file_type: str
    file_size: int = Field(ge=0, le=20000000)
    uploaded_at: AwareDatetime
    parse_options: ParseOptions
    available: bool
    unavailable_reason: UnavailableReason | None
    download_url: str | None


class AttachmentInfo(BaseModel):
    id: PositiveID
    attachment_id: PositiveID
    conversation_id: PositiveID
    message_id: PositiveID | None
    file_name: str
    file_path: str
    file_type: str
    file_size: int
    created_at: AwareDatetime
    updated_at: AwareDatetime
    parse_status: ParseStatus
    parse_error: str | None
    parse_options: ParseOptions
    parse_summary: list[dict[str, Any]] | None
    delete_status: AttachmentDeleteStatus
    delete_error: str | None
    can_retry: bool
    download_url: str | None


class AttachmentPage(Page[AttachmentInfo]):
    total_bytes: int


class ConversationInfo(BaseModel):
    id: PositiveID
    conversation_id: PositiveID
    title: str = Field(max_length=100)
    title_source: Literal["default", "auto", "manual"]
    status: ConversationStatus
    data_source_id: PositiveID | None
    data_source_locked: bool
    data_source_name: str | None
    data_source_status: Literal["enabled", "disabled", "removed", "not_published", "unselected"]
    current_task_id: PositiveID | None
    latest_task_id: PositiveID | None
    can_retry: bool
    cleanup_status: CleanupStatus
    cleanup_error: str | None
    cleanup_updated_at: AwareDatetime | None
    last_message_at: AwareDatetime | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class InputReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["attachment", "evidence", "output"]
    attachment_id: PositiveID | None = None
    task_id: PositiveID | None = None
    evidence_id: PositiveID | None = None
    output_id: PositiveID | None = None
    available: bool | None = None
    unavailable_reason: UnavailableReason | None = None

    @model_validator(mode="after")
    def reference_shape(self):
        supplied = {
            name for name in ("attachment_id", "task_id", "evidence_id", "output_id") if getattr(self, name) is not None
        }
        expected = {
            "attachment": {"attachment_id"},
            "evidence": {"task_id", "evidence_id"},
            "output": {"task_id", "output_id"},
        }
        if supplied != expected[self.type]:
            raise ValueError("Input reference fields must match its type")
        return self


class ToolOutput(BaseModel):
    output_id: PositiveID
    output_kind: Literal["stdout", "file"]
    relative_path: str
    generated_at: AwareDatetime
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_refs: list[InputReference] = Field(min_length=1)
    retention_kind: Literal["input_copy", "derived"]
    available: bool
    unavailable_reason: UnavailableReason | None

    @field_validator("relative_path")
    @classmethod
    def bounded_path(cls, value):
        return validate_relative_path(value)


class MessageInfo(BaseModel):
    id: PositiveID
    message_id: PositiveID
    task_id: PositiveID
    role: Literal["user", "assistant", "tool"]
    message_type: Literal["user_input", "assistant_text", "tool_call"]
    content: str
    seq_no: int = Field(ge=1)
    message_status: MessageStatus
    tool_name: str | None
    tool_status: Literal["running", "success", "failed", "cancelled"] | None
    tool_call_id: str | None
    tool_input: dict[str, Any] | None
    tool_outputs: list[ToolOutput]
    attachments: list[AttachmentSnapshot]
    created_at: AwareDatetime
    updated_at: AwareDatetime


class HistoryPage(Page[MessageInfo]):
    conversation: ConversationInfo
    snapshot_max_seq_no: int = Field(ge=0)


class ExportInfo(BaseModel):
    status: ExportStatus
    error_message: str | None
    generated_at: AwareDatetime | None


class ExportStates(BaseModel):
    markdown: ExportInfo
    html: ExportInfo


class TaskSnapshot(BaseModel):
    task_id: PositiveID
    conversation_id: PositiveID
    message_id: PositiveID
    assistant_message_id: PositiveID | None
    config_version: int | None
    final_report_scope: dict[str, Any] | None
    task_status: TaskStatus
    task_kind: TaskKind
    current_step: str = Field(max_length=200)
    cancel_requested_at: AwareDatetime | None
    is_cancelling: bool
    result_id: PositiveID | None
    messages: list[MessageInfo]
    export_status: ExportStates
    last_event_id: int = Field(ge=0)
    created_at: AwareDatetime
    started_at: AwareDatetime | None
    finished_at: AwareDatetime | None
    error_message: str | None


class Metric(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    metric_name: str = Field(min_length=1)
    metric_value: Annotated[int, Field(strict=True)] | Annotated[float, Field(strict=True)] | None
    metric_unit: str = Field(description="必填单位，metric_value为null时也必须填写，例如行/人/元/%")
    metric_period: str = Field(min_length=1, description="必填统计时期/范围，未知值也须明示范围或未确定")
    calculation_note: str = ""

    @model_validator(mode="after")
    def explain_missing(self):
        if self.metric_value is None and not self.calculation_note.strip():
            raise ValueError("Unknown metrics require a reason")
        return self


class AnalysisOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    problem_definition: str = Field(min_length=1)
    key_metrics: list[Metric]
    evidence_list: list[Evidence]
    conclusion_text: str = Field(min_length=1)
    missing_data_text: str = Field(min_length=1)
    next_action_text: str = Field(
        min_length=1, description="至少两条不同建议，使用换行或分号分开，每条说明动作和待验证问题"
    )

    @model_validator(mode="after")
    def distinct_evidence_and_actions(self):
        import re

        ids = [item.evidence_id for item in self.evidence_list]
        action_text = self.next_action_text.replace("\\n", "\n")
        marker = r"(?:[1-9][0-9]*[.、)）](?![0-9])|[一二三四五六七八九十]+[、)）]|建议[一二三四五六七八九十0-9]+[：:])"
        markers = list(re.finditer(marker, action_text))
        pieces = (
            [
                action_text[item.end() : markers[index + 1].start() if index + 1 < len(markers) else len(action_text)]
                for index, item in enumerate(markers)
            ]
            if len(markers) >= 2
            else re.split(r"[\r\n;；]+", action_text)
        )
        actions = {re.sub(r"^\s*(?:[-*]|\d+[.、)])\s*", "", line).strip() for line in pieces if line.strip()}
        if len(ids) != len(set(ids)) or len(actions) < 2:
            raise ValueError("Evidence must be unique and at least two distinct actions are required")
        return self


class ResultInfo(AnalysisOutput):
    result_id: PositiveID
    task_id: PositiveID
    conversation_id: PositiveID
    task_kind: TaskKind
    final_report_scope: dict[str, Any] | None
    result_markdown: str
    export_status: ExportStates
    created_at: AwareDatetime


class SendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: PositiveID
    client_request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$", strict=True)
    content: str = Field(min_length=1, max_length=20000, strict=True)
    data_source_id: PositiveID
    task_kind: TaskKind = "analysis"
    attachment_ids: list[PositiveID] = Field(default_factory=list)

    @field_validator("content", mode="before")
    @classmethod
    def trim_content(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def valid_input(self):
        self.content = self.content.strip()
        if not self.content or len(set(self.attachment_ids)) != len(self.attachment_ids):
            raise ValueError("Input must be nonempty and attachment identifiers must be unique")
        return self
