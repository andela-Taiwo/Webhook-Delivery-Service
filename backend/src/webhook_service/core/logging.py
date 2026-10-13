"""Structured logging setup (structlog -> stdlib logging -> JSON).

Every log line carries: timestamp, level, logger name, request_id,
tenant_id, and — when a span is active — trace_id/span_id, so logs can be
pivoted to traces in the observability backend (Grafana/Tempo, Jaeger, etc).
"""

import logging
import sys

import orjson
import structlog
from opentelemetry import trace
from structlog.typing import EventDict, Processor

from webhook_service.core.config import settings
from webhook_service.core.context import get_request_id, get_tenant_id


def _add_request_context(_logger: object, _method_name: str, event_dict: EventDict) -> EventDict:
    if request_id := get_request_id():
        event_dict["request_id"] = request_id
    if tenant_id := get_tenant_id():
        event_dict["tenant_id"] = tenant_id
    return event_dict


def _add_trace_context(_logger: object, _method_name: str, event_dict: EventDict) -> EventDict:
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict


def configure_logging() -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=settings.log_level,
    )

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        _add_request_context,
        _add_trace_context,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    def _orjson_str(obj: object, **_: object) -> str:
        return orjson.dumps(obj).decode()

    renderer = (
        structlog.processors.JSONRenderer(serializer=_orjson_str)
        if settings.log_json
        else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(settings.log_level)

    # Quiet down noisy third-party loggers; keep uvicorn access logs but
    # route them through the same structured pipeline.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).handlers = [handler]
        logging.getLogger(name).propagate = False


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    # structlog.get_logger is typed as returning Any because the concrete
    # class depends on runtime configuration; ours is fixed by
    # `wrapper_class=structlog.stdlib.BoundLogger` above.
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
