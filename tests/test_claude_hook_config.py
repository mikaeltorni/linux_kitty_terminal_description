"""Tests for install-time Claude hook settings wiring."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "claude_hook_config.py"


def load_module():
    spec = importlib.util.spec_from_file_location("claude_hook_config", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_configure_user_prompt_submit_hook_preserves_existing_hooks(tmp_path):
    mod = load_module()
    settings_file = tmp_path / ".claude" / "settings.json"
    settings_file.parent.mkdir()
    settings_file.write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {"matcher": "", "hooks": [{"type": "command", "command": "echo keep"}]}
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    mod.configure_user_prompt_submit_hook(settings_file, "kitty-terminal-description claude-hook")
    mod.configure_user_prompt_submit_hook(settings_file, "kitty-terminal-description claude-hook")

    settings = json.loads(settings_file.read_text(encoding="utf-8"))
    commands = [
        hook["command"]
        for entry in settings["hooks"]["UserPromptSubmit"]
        for hook in entry["hooks"]
    ]
    assert commands.count("kitty-terminal-description claude-hook") == 1
    assert "echo keep" in commands
