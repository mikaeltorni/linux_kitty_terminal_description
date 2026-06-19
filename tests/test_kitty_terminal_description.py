#!/usr/bin/env python3
"""Tests for kitty_terminal_description."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "kitty_terminal_description.py"


def load_module():
    """Load the executable CLI as a module."""
    spec = importlib.util.spec_from_file_location("kitty_terminal_description", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_clean_label_strips_quotes_reasoning_and_caps_words():
    """Model output cleanup should keep titles terse and safe."""
    mod = load_module()

    assert mod.clean_label('"Persistent dark mode toggle."') == "Persistent dark mode toggle"
    assert mod.clean_label("<think>notes</think>Fix queue race") == "Fix queue race"
    assert mod.clean_label("one two three four five", max_words=3) == "one two three"


def test_describe_prompt_falls_back_without_key(monkeypatch, tmp_path):
    """Missing OpenRouter credentials should still produce a raw prompt title."""
    mod = load_module()
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setattr(mod, "git_summary_for_project", lambda project_path="": "main")

    result = mod.describe_prompt("fix failing tests", "/repo/example_project")

    assert result["status"] == mod.STATUS_NO_KEY
    assert result["title"] == "fix failing tests · Example project · main"


def test_completion_message_reads_cached_feature(monkeypatch, tmp_path):
    """Completion text should use the feature label stored by title generation."""
    mod = load_module()
    monkeypatch.setattr(mod, "STATE_DIR", tmp_path / "state")

    mod.store_project_feature_label("/repo/example", "flaky test fix")

    assert mod.completion_message("Example", "/repo/example") == "Project Example done with flaky test fix"


def test_claude_hook_sets_title_from_prompt(monkeypatch, tmp_path, capsys):
    """Claude UserPromptSubmit payloads should drive title generation."""
    mod = load_module()
    payload = {"hook_event_name": "UserPromptSubmit", "prompt": "fix tests", "cwd": "/repo/example"}
    captured = {}

    monkeypatch.setattr(mod, "describe_prompt", lambda prompt, project_path: {"title": f"{prompt} title"})
    monkeypatch.setattr(mod, "set_terminal_prompt_title", lambda title, **kwargs: captured.update(title=title, kwargs=kwargs))
    monkeypatch.setattr(sys, "stdin", type("Stdin", (), {"read": lambda self: json.dumps(payload)})())

    assert mod.main(["claude-hook"]) == 0
    assert captured["title"] == "fix tests title"
    assert capsys.readouterr().out == ""
