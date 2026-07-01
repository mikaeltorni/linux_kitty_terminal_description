#!/usr/bin/env python3
"""Tests for the centralized project logging module."""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "scripts" / "logging_utils.py"


def load_module():
    """Load logging_utils from the scripts directory."""
    spec = importlib.util.spec_from_file_location("logging_utils", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_get_logger_writes_file_and_is_idempotent(monkeypatch, tmp_path):
    """File logging should create one handler and write under the log dir."""
    mod = load_module()
    monkeypatch.setenv("KITTY_TERMINAL_DESCRIPTION_LOG_DIR", str(tmp_path / ".log"))
    logger = logging.getLogger("test_file_logger")
    logger.handlers.clear()

    first = mod.get_logger("test_file_logger", "events.log")
    second = mod.get_logger("test_file_logger", "events.log")
    first.info("hello centralized logging")
    for handler in first.handlers:
        handler.flush()

    assert first is second
    assert len(first.handlers) == 1
    assert "hello centralized logging" in (tmp_path / ".log" / "events.log").read_text(
        encoding="utf-8"
    )
    first.handlers.clear()


def test_log_call_records_arguments_and_return(monkeypatch, tmp_path):
    """The standard decorator should record ENTER args and EXIT return value."""
    mod = load_module()
    monkeypatch.setenv("KITTY_TERMINAL_DESCRIPTION_LOG_DIR", str(tmp_path / ".log"))
    logger = logging.getLogger("test_call_logger")
    logger.handlers.clear()
    logger = mod.get_logger("test_call_logger", "trace.log")

    @mod.log_call(logger)
    def combine(left: str, right: str = "b") -> str:
        return f"{left}-{right}"

    assert combine("a", right="z") == "a-z"
    for handler in logger.handlers:
        handler.flush()

    contents = (tmp_path / ".log" / "trace.log").read_text(encoding="utf-8")
    assert "ENTER test_logging_utils.py:test_log_call_records_arguments_and_return" in contents
    assert "left='a'" in contents
    assert "right='z'" in contents
    assert "EXIT test_logging_utils.py:test_log_call_records_arguments_and_return" in contents
    assert "-> 'a-z'" in contents
    logger.handlers.clear()


def test_log_call_redacts_secret_arguments_and_returns(monkeypatch, tmp_path):
    """Call tracing must never serialize API keys, tokens, or secret values."""
    mod = load_module()
    monkeypatch.setenv("KITTY_TERMINAL_DESCRIPTION_LOG_DIR", str(tmp_path / ".log"))
    logger = logging.getLogger("test_secret_logger")
    logger.handlers.clear()
    logger = mod.get_logger("test_secret_logger", "trace.log")

    @mod.log_call(logger)
    def use_key(api_key: str, metadata: dict[str, str]) -> dict[str, str]:
        return {"token": api_key, **metadata}

    secret = "sk-or-v1-never-log-me"
    use_key(secret, {"safe": "value", "secret": secret})
    for handler in logger.handlers:
        handler.flush()

    contents = (tmp_path / ".log" / "trace.log").read_text(encoding="utf-8")
    assert secret not in contents
    assert "[REDACTED]" in contents
    logger.handlers.clear()
