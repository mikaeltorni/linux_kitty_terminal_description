#!/usr/bin/env python3
"""Structural tests for installer assets."""

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_installer_is_sudo_free_and_installs_cli():
    """The installer should install the CLI without unconditional sudo."""
    source = (ROOT / "scripts" / "install_standalone.sh").read_text(encoding="utf-8")

    assert 'install -D -m 0755 "$SCRIPT_DIR/scripts/kitty_terminal_description.py"' in source
    assert "sudo -H -u" in source
    assert "apt-get" not in source


def test_installer_configures_claude_but_not_kilo():
    """Standalone setup should wire Claude and intentionally ignore Kilo."""
    source = (ROOT / "scripts" / "install_standalone.sh").read_text(encoding="utf-8")

    assert "UserPromptSubmit" in source
    assert "kitty-terminal-description claude-hook" in source
    assert "kilo" not in source.lower()


def test_env_and_logs_are_ignored():
    """Secrets and runtime logs must stay out of git."""
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert ".env" in ignore
    assert ".log/" in ignore
