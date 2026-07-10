#!/usr/bin/env python3
"""Tests for the shared OpenRouter/Qwen conventional commit generator."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "kitty_terminal_description.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("kitty_terminal_description_commit", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_generate_commit_message_uses_qwen_and_returns_conventional_subject(monkeypatch):
    mod = _load_module()
    captured = {}

    def urlopen(req, timeout=None):
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return _Response({"choices": [{"message": {"content": "feat(review): add worktree diff navigation"}}]})

    monkeypatch.setattr(mod.request, "urlopen", urlopen)

    result = mod.generate_commit_message(
        "diff --git a/src/a.py b/src/a.py\n+new behavior",
        project_path="/repo/demo",
        key="secret",
    )

    assert result.status == mod.STATUS_OK
    assert result.label == "feat(review): add worktree diff navigation"
    assert captured["payload"]["model"] == mod.DEFAULT_SUMMARY_MODEL
    assert "conventional commit" in captured["payload"]["messages"][0]["content"].lower()


def test_commit_message_cleanup_rejects_nonconventional_or_multiline_output():
    mod = _load_module()

    assert mod.clean_commit_subject("fix(ui): correct colors\nextra explanation") == "fix(ui): correct colors"
    assert mod.clean_commit_subject("Here is your commit message") == ""
    assert mod.clean_commit_subject("feat: " + "x" * 200) == ""


def test_commit_message_cli_reads_diff_from_stdin_and_fails_without_key(monkeypatch, tmp_path, capsys):
    mod = _load_module()
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(mod, "env_file_candidates", lambda: [tmp_path / "missing.env"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("diff --git a/a b/a\n+line"))

    status = mod.main(["commit-message", "--project-path", "/repo/demo"])

    assert status != 0
    assert "OPENROUTER_API_KEY" in capsys.readouterr().err
