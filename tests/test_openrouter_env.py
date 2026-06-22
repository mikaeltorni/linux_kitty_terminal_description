#!/usr/bin/env python3
"""Tests for the OpenRouter env-file helper owned by this repository.

The copy-only-OPENROUTER_API_KEY logic used to live in Agent Command Center; it
now lives here as the single source of truth so any consumer can hand over its
own ``.env`` without leaking unrelated secrets into the Kitty config.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "scripts" / "openrouter_env.py"


def load_module():
    """Load the openrouter_env helper module from the scripts directory."""
    spec = importlib.util.spec_from_file_location("openrouter_env", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_copy_openrouter_key_preserves_unrelated_env_values(tmp_path):
    """Copy only OPENROUTER_API_KEY and keep unrelated target values."""
    mod = load_module()
    source = tmp_path / ".env"
    target = tmp_path / "openrouter.env"
    source.write_text("OTHER=source\nexport OPENROUTER_API_KEY=secret\n", encoding="utf-8")
    target.write_text("KEEP=1\nOPENROUTER_API_KEY=old\n", encoding="utf-8")

    assert mod.copy_openrouter_key(source, target) is True

    assert target.read_text(encoding="utf-8") == "KEEP=1\nexport OPENROUTER_API_KEY=secret\n"
    assert oct(target.stat().st_mode & 0o777) == "0o600"


def test_copy_openrouter_key_skips_when_absent(tmp_path):
    """No OPENROUTER_API_KEY in source means nothing is copied."""
    mod = load_module()
    source = tmp_path / ".env"
    target = tmp_path / "openrouter.env"
    source.write_text("OTHER=source\n", encoding="utf-8")

    assert mod.copy_openrouter_key(source, target) is False
    assert not target.exists()


def test_installer_uses_overridable_source_env_and_key_only_copy():
    """The installer honors the consumer source-env override via key-only copy."""
    source = (ROOT / "scripts" / "install_standalone.sh").read_text(encoding="utf-8")

    assert 'KITTY_TERMINAL_DESCRIPTION_SOURCE_ENV:-$SCRIPT_DIR/.env' in source
    assert "openrouter_env.py" in source
    # The old whole-file copy (which would leak unrelated secrets) is gone.
    assert 'install -m 0600 "$source_env" "$target_env"' not in source
