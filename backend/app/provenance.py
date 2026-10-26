"""Resolve persisted input references within one conversation, including indirect copies."""

from copy import deepcopy

from sqlalchemy import select

from app.attachments import conversation_path, original_path
from app.models import AnalysisResult, AnalysisTask, Attachment, Message


class Sources:
    @classmethod
    async def load(cls, db, cid):
        self = cls()
        self.tasks = {
            str(t.id): t
            for t in (await db.scalars(select(AnalysisTask).where(AnalysisTask.conversation_id == cid))).all()
        }
        self.attachments = {
            str(a.id): a for a in (await db.scalars(select(Attachment).where(Attachment.conversation_id == cid))).all()
        }
        self.messages = (
            await db.scalars(select(Message).where(Message.conversation_id == cid, Message.role == "tool"))
        ).all()
        self.outputs = {(str(m.task_id), o["output_id"]): o for m in self.messages for o in m.tool_outputs_json}
        self.evidence = {
            (str(r.task_id), e["evidence_id"]): e["source_locator"]
            for r in (await db.scalars(select(AnalysisResult).where(AnalysisResult.conversation_id == cid))).all()
            for e in r.evidence_list_json
        }
        # Evidence can feed a command before an analysis result exists.
        import json

        for message in self.messages:
            try:
                e = json.loads(message.content).get("evidence")
                if e:
                    self.evidence.setdefault((str(message.task_id), e["evidence_id"]), e["source_locator"])
            except (ValueError, AttributeError):
                pass
        return self

    def dependencies(self, ref, seen=None):
        seen = set() if seen is None else seen
        key = (
            ref["type"],
            ref.get("task_id"),
            ref.get("attachment_id") or ref.get("output_id") or ref.get("evidence_id"),
        )
        if key in seen:
            raise ValueError("Cyclic input reference")
        seen = seen | {key}
        if ref["type"] == "attachment":
            return {ref["attachment_id"]}
        if ref["type"] == "evidence":
            locator = self.evidence[(ref["task_id"], ref["evidence_id"])]
            if locator.get("source_task_id"):
                return self.dependencies(
                    {
                        "type": "evidence",
                        "task_id": locator["source_task_id"],
                        "evidence_id": locator["source_evidence_id"],
                    },
                    seen,
                )
            if "attachment_id" in locator:
                return {locator["attachment_id"]}
            if "output_id" not in locator:
                return set()
            return self.dependencies(
                {"type": "output", "task_id": locator["task_id"], "output_id": locator["output_id"]}, seen
            )
        artifact = self.outputs[(ref["task_id"], ref["output_id"])]
        if not artifact.get("input_refs"):
            raise ValueError("Missing input provenance")
        return set().union(*(self.dependencies(r, seen) for r in artifact["input_refs"]))

    def input_reason(self, refs):
        try:
            ids = set().union(*(self.dependencies(ref) for ref in refs))
        except (KeyError, ValueError, TypeError):
            return "storage_missing"
        for aid in sorted(ids):
            attachment = self.attachments.get(aid)
            if not attachment:
                return "attachment_deleted"
            if attachment.delete_status != "none":
                return "deleting" if attachment.delete_status == "deleting" else "delete_failed"
            task = next(iter(self.tasks.values()), None)
            if task:
                try:
                    if not original_path(attachment, task.user_id).is_file():
                        return "storage_missing"
                except (OSError, ValueError):
                    return "storage_missing"
        return None

    def output_path(self, tid, output):
        task = self.tasks[tid]
        return conversation_path(
            task.user_id, task.conversation_id, "workspace", "tasks", tid, "output", *output["relative_path"].split("/")
        )

    def output_info(self, tid, output):
        value = deepcopy(output)
        for ref in value["input_refs"]:
            reason = self.input_reason([ref])
            ref.update(available=reason is None, unavailable_reason=reason)
        reason = (
            self.input_reason(value["input_refs"])
            if value.get("retention_kind", "input_copy") == "input_copy"
            else None
        )
        try:
            if reason is None and not self.output_path(tid, value).is_file():
                reason = "storage_missing"
        except (KeyError, ValueError, OSError):
            reason = "storage_missing"
        value.update(available=reason is None, unavailable_reason=reason)
        return value
