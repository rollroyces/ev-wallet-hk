"""Structured logging for EV Wallet HK.

Single entry point :func:`get_logger`. Idempotent :func:`configure` so any
module can call it without worrying about ordering. Supports both ``human``
(rich) and ``json`` (machine-readable) output via the
``EVW_LOG_FORMAT`` setting.

The format chosen here deliberately does NOT ship a JSON serializer dep —
the architecture contract says JSON is a hint for the log aggregator, not
a hard requirement of the application. When ``log_format=json``, we use
:mod:`pythonjsonlogger` if available, otherwise we fall back to a
std-lib-only JSON formatter that constructs the line manually.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

# Module-level state — guarded by ``_CONFIGURED`` so multiple ``configure()``
# calls are no-ops after the first.
_CONFIGURED = False
_DEFAULT_FORMAT = (
    "%(asctime)s %(levelname)-8s %(name)s [%(trace_id)s] %(message)s"
)


class _TraceIdFilter(logging.Filter):
    """Inject ``trace_id`` = ``'-'`` into every record.

    The trace_id middleware replaces this with a real ULID per request by
    mutating the formatter's record factory, but the filter guarantees the
    attribute is always present so format strings never KeyError.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "trace_id"):
            record.trace_id = "-"
        return True


class _SafeJsonFormatter(logging.Formatter):
    """JSON formatter that works without :mod:`pythonjsonlogger`.

    Includes timestamp, level, logger name, message, trace_id, and any
    ``extra=`` fields the caller passed. Used only when ``pythonjsonlogger``
    is unavailable — keeps the package's import surface small.
    """

    _RESERVED = {
        "name", "msg", "args", "levelname", "levelno", "pathname",
        "filename", "module", "exc_info", "exc_text", "stack_info",
        "lineno", "funcName", "created", "msecs", "relativeCreated",
        "thread", "threadName", "processName", "process", "message",
        "asctime", "trace_id",
    }

    def format(self, record: logging.LogRecord) -> str:
        import json
        from datetime import datetime, timezone

        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "trace_id": getattr(record, "trace_id", "-"),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack_info"] = record.stack_info
        # Surface extras
        for key, value in record.__dict__.items():
            if key not in self._RESERVED and not key.startswith("_"):
                try:
                    json.dumps(value)
                    payload[key] = value
                except (TypeError, ValueError):
                    payload[key] = repr(value)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure(
    level: str | int | None = None,
    fmt: str | None = None,
    *,
    stream: Any | None = None,
) -> None:
    """Configure the root logger once (idempotent).

    Args:
        level: Optional level override (``"INFO"`` or ``logging.INFO``).
            If ``None``, the level is read from :class:`Settings.log_level`.
        fmt: Optional format override (``"human"`` or ``json"``).
            If ``None``, the format is read from :class:`Settings.log_format`.
        stream: Optional stream to log to (defaults to ``stderr``).

    Notes:
        Subsequent calls are no-ops. Tests that need to reconfigure should
        call :func:`reset_logging` first.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    # Lazy import to avoid a circular dep at module load time.
    resolved_level = level
    resolved_fmt = fmt
    try:
        from evwallet.config import get_settings  # noqa: PLC0415
    except Exception:
        get_settings = None  # type: ignore[assignment]
    else:
        try:
            settings = get_settings()
        except Exception:
            settings = None
        if resolved_level is None:
            resolved_level = settings.log_level if settings else "INFO"
        if resolved_fmt is None:
            resolved_fmt = settings.log_format if settings else "human"

    if resolved_level is None:
        resolved_level = "INFO"
    if resolved_fmt is None:
        resolved_fmt = "human"

    handler = logging.StreamHandler(stream or sys.stderr)
    handler.addFilter(_TraceIdFilter())

    if str(resolved_fmt).lower() == "json":
        try:
            from pythonjsonlogger.json import JsonFormatter  # noqa: PLC0415
        except ImportError:
            handler.setFormatter(_SafeJsonFormatter())
        else:
            handler.setFormatter(
                JsonFormatter(
                    "%(asctime)s %(levelname)s %(name)s %(trace_id)s %(message)s",
                    rename_fields={
                        "asctime": "ts",
                        "levelname": "level",
                        "name": "logger",
                    },
                )
            )
    else:
        handler.setFormatter(logging.Formatter(_DEFAULT_FORMAT))

    root = logging.getLogger()
    # Replace handlers so re-import doesn't accumulate duplicates.
    root.handlers = [handler]
    root.setLevel(
        resolved_level if isinstance(resolved_level, int)
        else getattr(logging, str(resolved_level).upper(), logging.INFO)
    )
    # Silence noisy libraries unless we're explicitly in DEBUG.
    if root.level > logging.DEBUG:
        for noisy in ("uvicorn.access", "sqlalchemy.engine", "httpx"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True


def reset_logging() -> None:
    """Reset logging state so :func:`configure` can run again (testing only)."""
    global _CONFIGURED
    logging.getLogger().handlers = []
    _CONFIGURED = False


def get_logger(name: str) -> logging.Logger:
    """Return a logger for *name*, calling :func:`configure` if needed.

    Args:
        name: Logger name — typically ``__name__``.

    Returns:
        A :class:`logging.Logger` ready to use.
    """
    if not _CONFIGURED:
        configure()
    return logging.getLogger(name)


__all__ = ["configure", "get_logger", "reset_logging"]
