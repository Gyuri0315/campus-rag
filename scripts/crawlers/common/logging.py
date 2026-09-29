"""Shared human-readable and JSONL logging for crawler processes."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from scripts.crawlers.common.schema import KST


COMMON_EVENTS = {
    "content_validation",
    "url_skipped",
    "run_started", "run_finished", "section_started", "section_finished",
    "list_fetched", "document_discovered", "document_saved",
    "document_unchanged", "document_skipped", "document_deleted",
    "document_failed", "attachment_downloaded", "attachment_failed",
    "state_loaded", "state_saved", "retry_scheduled", "request_failed",
    "tls_fallback_used",
    "batch_started", "batch_progress", "batch_finished",
    "probe_started", "probe_finished", "discovery_started", "discovery_finished",
    "batch_item_skipped", "batch_item_failed",
}
SENSITIVE_KEYS = {
    "authorization", "cookie", "set-cookie", "password", "passwd", "secret",
    "token", "access_token", "refresh_token", "api_key", "apikey", "key",
}
MASK = "***REDACTED***"
_HANDLER_MARKER = "_crawler_structured_handler"


def _file_logging_enabled_by_default() -> bool:
    """Keep operational files out of unit-test runs unless explicitly enabled."""
    configured = os.environ.get("CRAWLER_FILE_LOGGING")
    if configured is not None:
        return configured.strip().lower() not in {"0", "false", "no", "off"}
    return "unittest" not in sys.modules and "pytest" not in sys.modules


def _is_sensitive_key(key: object) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(key).strip().lower()).strip("_")
    compact = normalized.replace("_", "")
    return (
        normalized in SENSITIVE_KEYS
        or compact in {"authorization", "cookie", "setcookie", "password", "passwd", "secret", "token", "accesstoken", "refreshtoken", "apikey"}
        or compact.endswith(("password", "passwd", "secret", "token", "apikey"))
        or normalized.endswith(("_password", "_secret", "_token", "_api_key"))
    )


class LogContext:
    def __init__(self, dataset: str, run_id: str = "-") -> None:
        self.dataset = dataset
        self.run_id = run_id


def sanitize_url(value: str) -> str:
    try:
        parts = urlsplit(value)
    except ValueError:
        return value
    if not parts.scheme or not parts.netloc:
        return value
    hostname = parts.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname
    if parts.port is not None:
        netloc += f":{parts.port}"
    if parts.username is not None or parts.password is not None:
        netloc = f"{MASK}@{netloc}"
    query = []
    for key, item in parse_qsl(parts.query, keep_blank_values=True):
        query.append((key, MASK if _is_sensitive_key(key) else item))
    return urlunsplit((parts.scheme, netloc, parts.path, urlencode(query), parts.fragment))


def sanitize_log_value(value: Any, key: str | None = None) -> Any:
    if key and _is_sensitive_key(key):
        return MASK
    if isinstance(value, dict):
        return {str(k): sanitize_log_value(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [sanitize_log_value(item) for item in value]
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, str):
        sanitized = sanitize_url(value)
        sanitized = re.sub(
            r"(?i)\b(authorization|cookie|password|secret|token|api[_-]?key)\s*[:=]\s*([^\s,;]+)",
            lambda match: f"{match.group(1)}={MASK}", sanitized,
        )
        return sanitized
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


class CrawlerContextFilter(logging.Filter):
    def __init__(self, context: LogContext) -> None:
        super().__init__()
        self.context = context

    def filter(self, record: logging.LogRecord) -> bool:
        record.dataset = getattr(record, "dataset", None) or self.context.dataset
        record.run_id = getattr(record, "run_id", None) or self.context.run_id
        record.event = getattr(record, "event", None) or "log_message"
        record.context = sanitize_log_value(getattr(record, "context", {}) or {})
        return True


def _timestamp(record: logging.LogRecord) -> str:
    return datetime.fromtimestamp(record.created, KST).isoformat(timespec="seconds")


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = sanitize_log_value(record.getMessage())
        context = dict(getattr(record, "context", {}) or {})
        context.setdefault("run_id", record.run_id)
        pairs = " ".join(
            f"{key}={json.dumps(value, ensure_ascii=False, separators=(',', ':'))}"
            for key, value in context.items()
        )
        output = f"{_timestamp(record)} {record.levelname} {record.dataset} {record.event}"
        if pairs:
            output += f" {pairs}"
        if message and message != record.event:
            output += f" message={json.dumps(message, ensure_ascii=False)}"
        if record.exc_info and (record.levelno >= logging.ERROR or record.levelno <= logging.DEBUG):
            output += "\n" + str(sanitize_log_value(self.formatException(record.exc_info)))
        return output


class JsonlFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = dict(getattr(record, "context", {}) or {})
        payload.update({
            "timestamp": _timestamp(record), "level": record.levelname,
            "dataset": record.dataset, "run_id": record.run_id, "event": record.event,
        })
        message = sanitize_log_value(record.getMessage())
        if message and message != record.event:
            payload["message"] = message
        if record.exc_info and (record.levelno >= logging.ERROR or record.levelno <= logging.DEBUG):
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(sanitize_log_value(payload), ensure_ascii=False, separators=(",", ":"))


def configure_crawler_logging(
    dataset: str,
    project_root: Path,
    *,
    level: int = logging.INFO,
    debug: bool = False,
    console_stream: TextIO | None = None,
    file_logging: bool | None = None,
) -> tuple[logging.Logger, LogContext]:
    logger = logging.getLogger(f"crawler.{dataset}")
    logger.setLevel(logging.DEBUG if debug else level)
    logger.propagate = False
    for handler in list(logger.handlers):
        if getattr(handler, _HANDLER_MARKER, False):
            logger.removeHandler(handler)
            handler.close()

    context = LogContext(dataset)
    context_filter = CrawlerContextFilter(context)

    handlers: list[logging.Handler] = [logging.StreamHandler(console_stream or sys.stdout)]
    if file_logging if file_logging is not None else _file_logging_enabled_by_default():
        log_dir = project_root / "logs" / "crawlers"
        log_dir.mkdir(parents=True, exist_ok=True)
        text_handler = logging.FileHandler(log_dir / f"{dataset}.log", encoding="utf-8")
        text_handler.setFormatter(ConsoleFormatter())
        handlers.append(text_handler)
        jsonl_handler = logging.FileHandler(log_dir / f"{dataset}.jsonl", encoding="utf-8")
        jsonl_handler.setFormatter(JsonlFormatter())
        handlers.append(jsonl_handler)
    for index, handler in enumerate(handlers):
        setattr(handler, _HANDLER_MARKER, True)
        handler.setLevel(logging.DEBUG if debug else level)
        handler.addFilter(context_filter)
        if handler.formatter is None:
            handler.setFormatter(ConsoleFormatter())
        logger.addHandler(handler)
    return logger, context


def set_run_id(context: LogContext, run_id: str) -> None:
    context.run_id = run_id


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    *,
    exc_info: Any = None,
    **context: Any,
) -> None:
    if event not in COMMON_EVENTS:
        raise ValueError(f"unsupported crawler log event: {event}")
    logger.log(level, event, extra={"event": event, "context": context}, exc_info=exc_info)


def log_attachment_events(
    logger: logging.Logger, attachments: object, *, source_id: object = None
) -> None:
    if not isinstance(attachments, list):
        return
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        if attachment.get("downloaded") is True:
            log_event(
                logger, logging.INFO, "attachment_downloaded", source_id=source_id,
                attachment_id=attachment.get("id"), name=attachment.get("name"),
                url=attachment.get("final_url") or attachment.get("url"),
                size_bytes=attachment.get("size_bytes"),
            )
        elif attachment.get("error") is not None:
            log_event(
                logger, logging.ERROR, "attachment_failed", source_id=source_id,
                attachment_id=attachment.get("id"), name=attachment.get("name"),
                url=attachment.get("url"), error=attachment.get("error"),
            )
