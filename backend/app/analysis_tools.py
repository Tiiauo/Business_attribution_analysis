import hashlib
import json
import re
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from uuid import uuid4

import asyncpg
import sqlglot
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from app.auth import now
from app.contracts import Evidence
from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.security import safe_path
from app.models import AnalysisResult, AnalysisTask, Message
from app.runtime import resolve_credential
from app.sandbox import SCRIPT_VERSION, run_command

SAFE_FUNCTIONS = {
    "AND",
    "OR",
    "EXISTS",
    "CASE",
    "IF",
    "TIMESTAMP_TRUNC",
    "TIME_TO_STR",
    "STR_POSITION",
    "TS_OR_DS_TO_DATE",
} | set(
    "COUNT SUM AVG MIN MAX ABS ROUND CEIL FLOOR COALESCE NULLIF CAST TRY_CAST DATE_TRUNC DATE_PART EXTRACT TO_CHAR LOWER UPPER TRIM LENGTH SUBSTRING CONCAT CONCAT_WS ROW_NUMBER RANK DENSE_RANK LAG LEAD FIRST_VALUE LAST_VALUE NTILE PERCENTILE_CONT PERCENTILE_DISC STDDEV STDDEV_POP STDDEV_SAMP VARIANCE VAR_POP VAR_SAMP BOOL_AND BOOL_OR STRING_AGG ARRAY_AGG CURRENT_DATE CURRENT_TIMESTAMP NOW GREATEST LEAST".split()
)


def readonly_query(query, schema):
    try:
        statements = sqlglot.parse(query, read="postgres")
        if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.Union, exp.Intersect, exp.Except)):
            raise ValueError()
        tree = statements[0]
        if any(isinstance(node, (exp.DML, exp.DDL, exp.Command, exp.Into, exp.Lock)) for node in tree.walk()):
            raise ValueError()
        for scope in traverse_scope(tree):
            for _, source in scope.sources.items():
                if isinstance(source, exp.Table) and (
                    source.catalog or source.db != schema or not isinstance(source.this, exp.Identifier)
                ):
                    raise ValueError()
        for node in tree.walk():
            if isinstance(node, exp.Func):
                name = node.name.upper() if isinstance(node, exp.Anonymous) else node.sql_name()
                if name not in SAFE_FUNCTIONS:
                    raise ValueError()
            if isinstance(node, exp.Dot):
                raise ValueError()
        return tree.sql(dialect="postgres")
    except (sqlglot.errors.SqlglotError, ValueError, AttributeError):
        raise ApiError(
            403, "SQL_NOT_ALLOWED", "仅允许绑定 schema 内的单条只读查询和批准函数；请显式限定表名。"
        ) from None


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Inspect(Arguments):
    pass


class Query(Arguments):
    query: str = Field(min_length=1, max_length=20000)
    filter_summary: str = Field(min_length=1, max_length=2000)


class ReadFile(Arguments):
    attachment_id: str = Field(pattern=r"^[1-9][0-9]{0,18}$")
    file_index: int = Field(default=0, ge=0)
    start: int = Field(default=0, ge=0)
    count: int = Field(default=50, ge=1, le=100)


class Search(ReadFile):
    query: str = Field(min_length=1, max_length=500)


class Command(Arguments):
    command: str
    evidence_id: str
    column: str = Field(default="", max_length=200)
    null_policy: str = "reject"
    query: str = Field(default="", max_length=500)


SCHEMAS = {
    "inspect_database": Inspect,
    "query_database": Query,
    "read_file": ReadFile,
    "search_text": Search,
    "run_command": Command,
}
DESCRIPTIONS = {
    "inspect_database": "读取当前绑定 schema 的业务表与字段（不取业务记录）",
    "query_database": "单条 PostgreSQL SELECT，所有业务表必须带绑定 schema；最多400行，超限报错不截断。记录筛选/去重/日期口径。",
    "read_file": "仅读取本轮显式选择的附件安全输入；分页位置从0开始。",
    "search_text": "检索本轮显式选择的安全附件，返回原始位置；超过100项需缩小范围。",
    "run_command": "批准脚本 python_stats/text_search；输入只能引用真实证据ID。统计须指定列、显式十进制转换与null_policy=reject；禁止自编代码。",
}


def dumps(value):
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


class AnalysisTools:
    def __init__(self, context, task):
        self.context, self.task = context, task
        self.config = task.config_snapshot_json
        self.source_id, self.source = next(iter(self.config["data_sources"].items()))
        self.evidence, self.data = {}, {}
        self.output_root = safe_path(
            get_settings().data_root,
            "workspace",
            str(task.user_id),
            str(task.conversation_id),
            "tasks",
            str(task.id),
            "output",
        )
        self.input_root = safe_path(
            get_settings().data_root,
            "workspace",
            str(task.user_id),
            str(task.conversation_id),
            "tasks",
            str(task.id),
            "input",
        )

    def definitions(self):
        permissions = self.config["tools"]
        names = []
        if self.task.task_kind == "analysis":
            if permissions["sql_enabled"]:
                names += ["inspect_database", "query_database"]
            if permissions["file_enabled"]:
                names.append("read_file")
            if permissions["search_enabled"]:
                names.append("search_text")
        if permissions["command_enabled"] and permissions["allowed_commands"]:
            names.append("run_command")
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": DESCRIPTIONS[name],
                    "parameters": SCHEMAS[name].model_json_schema(),
                },
            }
            for name in names
        ]

    def register(self, source_type, source_name, text, locator, data=None):
        eid = f"E{len(self.evidence) + 1:04d}"
        evidence = Evidence(
            evidence_id=eid,
            source_type=source_type,
            source_name=source_name,
            evidence_text=text,
            related_metric="待模型注明所支撑的问题",
            confidence="high",
            source_locator=locator,
        )
        self.evidence[eid] = evidence.model_dump(mode="json", by_alias=True)
        if data is not None:
            self.data[eid] = data
        return self.evidence[eid]

    async def historical(self):
        async with self.context.sessions() as db:
            query = (
                select(AnalysisResult, AnalysisTask)
                .join(AnalysisTask, AnalysisTask.id == AnalysisResult.task_id)
                .where(
                    AnalysisTask.conversation_id == self.task.conversation_id,
                    AnalysisTask.user_id == self.task.user_id,
                    AnalysisTask.task_status == "success",
                    AnalysisTask.id < self.task.id,
                )
                .order_by(AnalysisTask.id)
            )
            if self.task.task_kind == "final_report":
                query = query.where(
                    AnalysisTask.id.in_(list(map(int, self.task.final_report_scope_json["source_task_ids"])))
                )
            rows = (await db.execute(query)).all()
            summaries = []
            for result, task in rows:
                for saved in result.evidence_list_json:
                    await verify_reference(db, task, saved, set())
                    value = deepcopy(saved)
                    for key in (
                        "available",
                        "unavailable_reason",
                        "availability_checked_at",
                        "available_at_result",
                        "unavailable_reason_at_result",
                    ):
                        value["source_locator"].pop(key, None)
                    value["source_locator"].update(source_task_id=str(task.id), source_evidence_id=saved["evidence_id"])
                    value["evidence_id"] = f"E{len(self.evidence) + 1:04d}"
                    value = Evidence.model_validate(value).model_dump(mode="json", by_alias=True)
                    self.evidence[value["evidence_id"]] = value
                    # Historical computations consume saved excerpts, never reopen old files.
                    try:
                        self.data[value["evidence_id"]] = json.loads(saved["evidence_text"])
                    except ValueError:
                        self.data[value["evidence_id"]] = saved["evidence_text"]
                summaries.append(
                    {
                        "task_id": str(task.id),
                        "problem": result.problem_definition,
                        "metrics": result.key_metrics_json,
                        "conclusion": result.conclusion_text,
                        "missing": result.missing_data_text,
                    }
                )
            return summaries

    async def connect(self):
        async with self.context.sessions() as db:
            secret = json.loads(await resolve_credential(db, self.source["credential_ref"]))
        return await asyncpg.connect(
            host=self.source["connection_target"]["host"],
            port=self.source["connection_target"]["port"],
            database=self.source["database_name"],
            user=secret["username"],
            password=secret["password"],
            timeout=5,
            server_settings={
                "statement_timeout": str(self.config["tools"]["sql_timeout_seconds"] * 1000),
                "lock_timeout": "1000",
                "search_path": "pg_catalog",
            },
        )

    async def database(self, arguments, *, inspect=False):
        connection = None
        try:
            connection = await self.connect()
            async with connection.transaction(readonly=True):
                if inspect:
                    rows = await connection.fetch(
                        """SELECT table_name,column_name,data_type FROM information_schema.columns
                        WHERE table_schema=$1 ORDER BY table_name,ordinal_position""",
                        self.source["schema_name"],
                    )
                    return {"schema": self.source["schema_name"], "columns": [dict(row) for row in rows]}
                query = readonly_query(arguments.query, self.source["schema_name"])
                statement = await connection.prepare(query)
                if len({column.name for column in statement.get_attributes()}) != len(statement.get_attributes()):
                    raise ApiError(422, "SQL_DUPLICATE_COLUMNS", "查询结果列名重复，请为表达式指定唯一别名。")
                cursor = await statement.cursor()
                rows = await cursor.fetch(401)
                if len(rows) > 400:
                    raise ApiError(422, "SQL_RESULT_TOO_LARGE", "查询超过400行，请聚合或缩小范围后重试。")
                data = [dict(row) for row in rows]
                text = dumps(data)
                if len(text.encode()) > 180000:
                    raise ApiError(422, "SQL_RESULT_TOO_LARGE", "查询结果超过180000字节，请缩小范围。")
                locator = {
                    "data_source_id": self.source_id,
                    "database": self.source["database_name"],
                    "schema": self.source["schema_name"],
                    "query_text": query,
                    "filter_summary": arguments.filter_summary,
                    "extracted_at": now(),
                }
                evidence = self.register("database", self.source["name"], text, locator, json.loads(text))
                return {"evidence": evidence, "row_count": len(data)}
        except (
            asyncpg.PostgresSyntaxError,
            asyncpg.UndefinedColumnError,
            asyncpg.UndefinedTableError,
            asyncpg.GroupingError,
            asyncpg.AmbiguousColumnError,
            asyncpg.DatatypeMismatchError,
            asyncpg.DataError,
        ):
            raise ApiError(
                422, "SQL_INVALID_QUERY", "查询字段、表名或聚合无效；请核对字段、FROM、GROUP BY 后修正查询。"
            ) from None
        except (asyncpg.PostgresError, OSError, TimeoutError, ValueError, KeyError):
            raise ApiError(422, "SQL_EXECUTION_FAILED", "只读查询失败，请核对字段、权限、类型及运行时限。") from None
        finally:
            if connection is not None:
                await connection.close(timeout=3)

    async def prepare_inputs(self):
        if self.task.task_kind == "final_report":
            return
        from app.core.security import require_storage

        require_storage()
        self.input_root.mkdir(parents=True, exist_ok=True)
        manifest = []
        for attachment in self.task.attachment_snapshot_json:
            aid = attachment["attachment_id"]
            root = safe_path(
                get_settings().data_root,
                "workspace",
                str(self.task.user_id),
                str(self.task.conversation_id),
                "parsed",
                aid,
            )
            try:
                value = json.loads(safe_path(root, "manifest.json").read_text(encoding="utf-8"))
                if value["attachment_id"] != aid or value["parse_options"] != attachment["parse_options"]:
                    raise ValueError()
                target = safe_path(self.input_root, aid)
                target.mkdir(exist_ok=True)
                for index, item in enumerate(value["files"]):
                    content = safe_path(root, item["relative_path"]).read_bytes()
                    if len(content) > 20000000:
                        raise ValueError()
                    name = f"{index}.json" if item["format"] == "json" else f"{index}.txt"
                    safe_path(target, name).write_bytes(content)
                    manifest.append(
                        {
                            "attachment_id": aid,
                            "file_index": index,
                            "file_name": attachment["file_name"],
                            "path": aid + "/" + name,
                            "format": item["format"],
                            "position": item["position"],
                            "parse_options": attachment["parse_options"],
                            "file_type": attachment["file_type"],
                            "locations": item.get("locations", []),
                            "cells": item.get("cells", []),
                        }
                    )
            except (OSError, ValueError, KeyError, TypeError):
                raise ApiError(409, "ATTACHMENT_INPUT_UNAVAILABLE", "附件安全输入不完整，请删除重传。") from None
        safe_path(self.input_root, "manifest.json").write_text(dumps(manifest), encoding="utf-8")
        self.manifest = manifest

    def file(self, arguments, *, search=False):
        item = next(
            (
                item
                for item in self.manifest
                if item["attachment_id"] == arguments.attachment_id and item["file_index"] == arguments.file_index
            ),
            None,
        )
        if item is None:
            raise ApiError(403, "FILE_NOT_AUTHORIZED", "仅读取本轮显式选择并成功解析的附件。")
        content = safe_path(self.input_root, *item["path"].split("/")).read_text(encoding="utf-8")
        rows = json.loads(content) if item["format"] == "json" else content.splitlines()
        positions = list(enumerate(rows, 1))
        if search:
            positions = [
                (index, row) for index, row in positions if arguments.query.casefold() in dumps(row).casefold()
            ]
            if len(positions) > 100:
                raise ApiError(422, "SEARCH_RESULT_TOO_LARGE", "匹配超过100项，请缩小检索范围。")
        else:
            positions = positions[arguments.start : arguments.start + arguments.count]
        locations = item.get("locations", [])
        preview = [
            {
                "position": locations[index - 1] if locations else {"line": index},
                "value": row,
                **({"cells": item["cells"][index - 1]} if item.get("cells") else {}),
            }
            for index, row in positions
        ]
        text = dumps(preview)
        if len(text.encode()) > 180000:
            raise ApiError(422, "FILE_RESULT_TOO_LARGE", "文件片段过大，请缩小读取范围。")
        from app.core.security import require_storage

        require_storage()
        self.output_root.mkdir(parents=True, exist_ok=True)
        oid = uuid4().hex
        safe_path(self.output_root, oid + ".json").write_text(text, encoding="utf-8")
        excerpt = dumps(
            {
                "returned": len(positions),
                "total": len(rows),
                "columns": item["position"].get("columns", []),
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
            }
        )
        evidence = self.register(
            "file",
            item["file_name"],
            excerpt,
            {
                "attachment_id": arguments.attachment_id,
                "file_name": item["file_name"],
                "position": {"part": item["position"], "items": [p["position"] for p in preview]},
            },
            [row for _, row in positions],
        )
        artifact = {
            "output_id": oid,
            "output_kind": "file",
            "relative_path": oid + ".json",
            "generated_at": now().isoformat(),
            "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "input_refs": [{"type": "attachment", "attachment_id": arguments.attachment_id}],
            "retention_kind": "input_copy",
            "available": True,
            "unavailable_reason": None,
        }
        return {"evidence": evidence, "total": len(rows), "returned": len(positions), "preview": preview}, [artifact]

    async def command(self, arguments, call_id):
        if arguments.command not in self.config["tools"]["allowed_commands"] or arguments.evidence_id not in self.data:
            raise ApiError(403, "COMMAND_NOT_ALLOWED", "命令或证据输入未授权。")
        evidence = self.evidence[arguments.evidence_id]
        source = self.data[arguments.evidence_id]
        if (
            self.task.task_kind == "final_report"
            and evidence["source_locator"].get("source_task_id")
            not in self.task.final_report_scope_json["source_task_ids"]
        ):
            raise ApiError(403, "EVIDENCE_OUT_OF_SCOPE", "计算输入必须直接来自固定报告范围。")
        if arguments.command == "python_stats":
            if arguments.null_policy != "reject" or not arguments.column or not isinstance(source, list):
                raise ApiError(
                    422, "NUMERIC_CONVERSION_REQUIRED", "统计须声明列与null_policy=reject，缺失及无效值不能静默排除。"
                )
            try:
                values = [Decimal(str(row[arguments.column])) for row in source]
                if not values or any(not value.is_finite() for value in values):
                    raise ValueError()
            except (InvalidOperation, ValueError, KeyError, TypeError):
                raise ApiError(
                    422, "NUMERIC_CONVERSION_FAILED", "所选列含缺失或不可转换数值，未执行统计。请明确修正口径。"
                ) from None
            data = {"values": list(map(str, values))}
        elif arguments.command == "text_search" and arguments.query:
            data = {"text": dumps(source), "query": arguments.query}
        else:
            raise ApiError(403, "COMMAND_NOT_ALLOWED", "命令或参数不在批准清单中。")
        output = await run_command(
            arguments.command, data, self.task.id, self.config["tools"]["command_timeout_seconds"]
        )
        json.loads(output)
        oid = uuid4().hex
        from app.core.security import require_storage

        require_storage()
        self.output_root.mkdir(parents=True, exist_ok=True)
        path = safe_path(self.output_root, oid + ".txt")
        path.write_bytes(output.encode())
        stamp = now().isoformat()
        locator = {
            "task_id": str(self.task.id),
            "tool_call_id": call_id,
            "output_id": oid,
            "output_kind": "stdout",
            "relative_path": path.name,
            "position": "完整stdout",
            "generated_at": stamp,
        }
        registered = self.register(
            "tool_output", arguments.command + " v" + SCRIPT_VERSION, output.strip(), locator, json.loads(output)
        )
        prior = evidence["source_locator"]
        ref = {
            "type": "evidence",
            "task_id": prior.get("source_task_id") or str(self.task.id),
            "evidence_id": prior.get("source_evidence_id") or evidence["evidence_id"],
        }
        artifact = {
            "output_id": oid,
            "output_kind": "stdout",
            "relative_path": path.name,
            "generated_at": stamp,
            "content_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "input_refs": [ref],
            "retention_kind": "derived" if arguments.command == "python_stats" else "input_copy",
            "available": True,
            "unavailable_reason": None,
        }
        return {"evidence": registered}, [artifact]

    async def call(self, name, arguments, call_id):
        if name not in {value["function"]["name"] for value in self.definitions()}:
            raise ApiError(403, "TOOL_NOT_ALLOWED", "本任务未授权该工具。")
        try:
            payload = SCHEMAS[name].model_validate(arguments)
        except ValidationError:
            raise ApiError(422, "TOOL_ARGUMENTS_INVALID", "工具参数不符合约定。") from None
        recorded = {**payload.model_dump(), **({"script_version": SCRIPT_VERSION} if name == "run_command" else {})}
        if name == "run_command" and payload.evidence_id in self.evidence:
            source = self.evidence[payload.evidence_id]["source_locator"]
            recorded["input_refs"] = [
                {
                    "type": "evidence",
                    "task_id": source.get("source_task_id") or str(self.task.id),
                    "evidence_id": source.get("source_evidence_id") or payload.evidence_id,
                }
            ]
        await self.context.tool_start(name, call_id, recorded)
        try:
            outputs = []
            if name in {"inspect_database", "query_database"}:
                result = await self.database(payload, inspect=name == "inspect_database")
            elif name in {"read_file", "search_text"}:
                result, outputs = self.file(payload, search=name == "search_text")
            else:
                result, outputs = await self.command(payload, call_id)
            await self.context.tool_finish(
                call_id, dumps({k: v for k, v in result.items() if k != "preview"}), outputs=outputs
            )
            return result
        except ApiError as error:
            await self.context.tool_finish(call_id, error.message, failed=True)
            raise


async def verify_reference(db, task, evidence, seen):
    locator = evidence["source_locator"]
    source_task_id = locator.get("source_task_id")
    if source_task_id:
        key = (source_task_id, locator.get("source_evidence_id"))
        if key in seen or not re.fullmatch(r"[1-9][0-9]{0,18}", source_task_id) or int(source_task_id) >= task.id:
            raise ApiError(422, "EVIDENCE_REFERENCE_INVALID", "历史证据引用顺序或关联不合法。")
        seen.add(key)
        row = (
            await db.execute(
                select(AnalysisTask, AnalysisResult)
                .join(AnalysisResult)
                .where(
                    AnalysisTask.id == int(source_task_id),
                    AnalysisTask.user_id == task.user_id,
                    AnalysisTask.conversation_id == task.conversation_id,
                    AnalysisTask.task_status == "success",
                )
            )
        ).one_or_none()
        if not row:
            raise ApiError(422, "EVIDENCE_REFERENCE_INVALID", "历史证据不存在或不属于本会话成功结果。")
        previous, result = row
        saved = next((item for item in result.evidence_list_json if item["evidence_id"] == key[1]), None)
        if (
            saved is None
            or saved["evidence_text"] != evidence["evidence_text"]
            or saved["source_type"] != evidence["source_type"]
            or saved["source_name"] != evidence["source_name"]
        ):
            raise ApiError(422, "EVIDENCE_REFERENCE_INVALID", "历史证据摘录不可修改。")
        metadata = {
            "input_refs",
            "source_task_id",
            "source_evidence_id",
            "available_at_result",
            "unavailable_reason_at_result",
            "available",
            "unavailable_reason",
            "availability_checked_at",
        }
        if {k: v for k, v in locator.items() if k not in metadata} != {
            k: v for k, v in saved["source_locator"].items() if k not in metadata
        }:
            raise ApiError(422, "EVIDENCE_REFERENCE_INVALID", "历史证据底层定位不可修改。")
        rank = {"low": 0, "medium": 1, "high": 2}
        if rank[evidence["confidence"]] > rank[saved["confidence"]]:
            raise ApiError(422, "EVIDENCE_REFERENCE_INVALID", "历史证据不能无新证据提高置信等级。")
        await verify_reference(db, previous, saved, seen)
        return
    # Root evidence must also exist in an actual persisted tool record.
    messages = (
        await db.scalars(
            select(Message).where(Message.task_id == task.id, Message.role == "tool", Message.tool_status == "success")
        )
    ).all()
    for message in messages:
        try:
            registered = json.loads(message.content).get("evidence")
        except (ValueError, AttributeError):
            continue
        if (
            registered
            and registered["evidence_id"] == evidence["evidence_id"]
            and registered["evidence_text"] == evidence["evidence_text"]
        ):
            fields = {
                "input_refs",
                "source_task_id",
                "source_evidence_id",
                "available",
                "unavailable_reason",
                "availability_checked_at",
                "available_at_result",
                "unavailable_reason_at_result",
            }
            if {k: v for k, v in locator.items() if k not in fields} == {
                k: v for k, v in registered["source_locator"].items() if k not in fields
            }:
                return
    raise ApiError(422, "EVIDENCE_REFERENCE_INVALID", "证据未关联实际成功工具输出。")
