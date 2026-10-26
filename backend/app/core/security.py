import hashlib
import hmac
import json
import logging
import re
import secrets
import shutil
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet

SENSITIVE = re.compile(
    r"password|secret|token|authorization|cookie|database_url|credential|api[_-]?key|prompt|content|input_text", re.I
)
logger = logging.getLogger("business_attribution")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())


def require_storage():
    from app.core.config import get_settings
    from app.core.errors import ApiError

    if shutil.disk_usage(get_settings().data_root).free < 5 * 1024**3:
        raise ApiError(507, "INSUFFICIENT_STORAGE", "可用磁盘不足5 GiB，已停止新增文件写入；仍可读取和清理已有资料。")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def random_token() -> str:
    return secrets.token_urlsafe(32)


def cipher(key_file: Path) -> Fernet:
    return Fernet(key_file.read_bytes().strip())


def csrf_token(session_token: str, key_file: Path) -> str:
    return hmac.new(key_file.read_bytes().strip(), session_token.encode(), hashlib.sha256).hexdigest()


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        counts = {
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "cached_tokens",
            "reasoning_tokens",
            "estimated_tokens",
        }
        return {
            key: item
            if key in counts and type(item) is int and item >= 0
            else "[REDACTED]"
            if SENSITIVE.search(str(key))
            else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return re.sub(r"(?i)([a-z][a-z0-9+.-]*://)[^\s/@]+:[^\s/@]+@", r"\1[REDACTED]@", value)
    return value


def log_event(event: str, **fields: Any) -> None:
    logger.info("%s %s", event, json.dumps(redact(fields), ensure_ascii=False, default=str))


def safe_path(root: Path, *parts: str) -> Path:
    root = root.resolve()
    current = root
    for part in parts:
        if not part or part in {".", ".."} or any(char in part for char in ("/", "\\", ":", "\x00")):
            raise ValueError("Invalid storage path component")
        current = current / part
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise ValueError("Storage links are not allowed")
    if not current.resolve().is_relative_to(root):
        raise ValueError("Storage path escapes its root")
    return current


def request_fingerprint(payload: dict) -> str:
    normalized = {
        "content": payload["content"].strip(),
        "data_source_id": payload["data_source_id"],
        "task_kind": payload.get("task_kind", "analysis"),
        "attachment_ids": sorted(payload.get("attachment_ids", [])),
    }
    return digest(json.dumps(normalized, ensure_ascii=False, separators=(",", ":")))
