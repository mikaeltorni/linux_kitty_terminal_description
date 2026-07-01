#!/usr/bin/env python3
"""Centralized logging for linux_kitty_terminal_description.

All Python scripts in this repository import ``get_logger`` and ``log_call``
from this module. Logging is best-effort, idempotent, and writes under the
repository ``.log/`` directory by default unless a caller supplies an override.
"""

from __future__ import annotations

import functools
import inspect
import logging
import os
from pathlib import Path
from typing import Any, Callable, TypeVar

LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"
F = TypeVar("F", bound=Callable[..., Any])
REDACTED = "[REDACTED]"
SENSITIVE_NAMES = ("api_key", "apikey", "authorization", "credential", "password", "secret", "token")


def _repo_root() -> Path:
    """Return the repository root relative to this module."""
    return Path(__file__).resolve().parents[1]


def _default_log_dir() -> Path:
    """Resolve the default log directory for scripts installed from this repo."""
    override = os.environ.get("KITTY_TERMINAL_DESCRIPTION_LOG_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    repo_root = _repo_root()
    if repo_root.name == "scripts":
        return Path.home() / "projects" / "linux_kitty_terminal_description" / ".log"
    return repo_root / ".log"


def get_logger(name: str, log_file: str, *, stream: bool = False) -> logging.Logger:
    """Return an idempotent project logger.

    Args:
        name: Logger name included in each record.
        log_file: File name inside the project log directory.
        stream: When true, write to stderr instead of a file. Installer helpers
            use this so diagnostics stay visible without changing stdout.

    Returns:
        A configured ``logging.Logger``. If file logging cannot be initialized,
        a ``NullHandler`` is attached so callers never fail because of logging.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    if logger.handlers:
        return logger

    try:
        if stream:
            handler: logging.Handler = logging.StreamHandler()
        else:
            log_dir = _default_log_dir()
            log_dir.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(log_dir / log_file, encoding="utf-8")
        handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
    except OSError:
        handler = logging.NullHandler()
    logger.addHandler(handler)
    return logger


def _redact(value: Any, name: str = "") -> Any:
    """Return a log-safe representation of potentially sensitive data."""
    normalized = name.lower().replace("-", "_")
    if any(marker in normalized for marker in SENSITIVE_NAMES):
        return REDACTED
    if isinstance(value, dict):
        return {key: _redact(item, str(key)) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact(item) for item in value)
    return value


def log_call(logger: logging.Logger, level: int = logging.DEBUG) -> Callable[[F], F]:
    """Trace a function by logging bound arguments on entry and return on exit.

    Args:
        logger: Centralized logger obtained from ``get_logger``.
        level: Logging level used for both trace records.

    Returns:
        A decorator for the callable being traced.
    """

    def decorator(func: F) -> F:
        location = f"{Path(func.__code__.co_filename).name}:{func.__qualname__}"
        signature = inspect.signature(func)

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            rendered = ", ".join(
                f"{name}={_redact(value, name)!r}" for name, value in bound.arguments.items()
            )
            logger.log(level, "ENTER %s(%s)", location, rendered)
            result = func(*args, **kwargs)
            logger.log(level, "EXIT %s -> %r", location, _redact(result, func.__name__))
            return result

        return wrapper  # type: ignore[return-value]

    return decorator
