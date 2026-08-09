#!/usr/bin/env python3
"""Structural and blank-install tests for installer assets."""

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_installer_is_sudo_free_and_installs_cli():
    """The installer should install the CLI and use the shared (sudo-free) helpers."""
    source = (ROOT / "scripts" / "install_standalone.sh").read_text(encoding="utf-8")

    assert 'install -D -m 0755 "$SCRIPT_DIR/scripts/kitty_terminal_description.py"' in source
    assert 'install -D -m 0644 "$SCRIPT_DIR/scripts/logging_utils.py"' in source
    # The sudo-free run_as_target now comes from the framework's
    # installer_helpers.sh (loaded via isc_load_helpers); no vendored copy here.
    assert "isc_load_helpers" in source
    assert "run_as_target() {" not in source
    assert "apt-get" not in source


def test_codex_hook_component_has_detect_function():
    """codex_hook must declare a detect function so it can report installed.

    The component creates no persistent artifact of its own (the prompt-title
    summary runs at agent launch inside the ACC tracker) and the framework's
    receipt fallback is unreliable across root/non-root runs, so without
    detect_codex_hook the component could never converge on a clean machine."""
    components = (ROOT / "installer" / "components.sh").read_text(encoding="utf-8")
    installer = (ROOT / "scripts" / "install_standalone.sh").read_text(encoding="utf-8")

    codex_line = next(
        line for line in components.splitlines() if line.strip().startswith('"codex_hook|')
    )
    assert "detect_codex_hook" in codex_line
    assert "detect_codex_hook()" in installer
    # Detection mirrors configure_codex_hook: installed when Codex is present.
    detect_body = installer.split("detect_codex_hook()", 1)[1].split("}", 1)[0]
    assert ".codex" in detect_body
    assert "command -v codex" in detect_body


def test_installer_configures_claude_but_not_kilo():
    """Standalone setup should wire Claude and intentionally ignore Kilo."""
    source = (ROOT / "scripts" / "install_standalone.sh").read_text(encoding="utf-8")

    assert "UserPromptSubmit" in source
    assert "kitty-terminal-description claude-hook" in source
    assert "claude_hook_config.py" in source
    assert "python3 - <<'PY'" not in source
    assert "kilo" not in source.lower()


def test_env_and_logs_are_ignored():
    """Secrets and runtime logs must stay out of git."""
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert ".env" in ignore
    assert ".log/" in ignore


def test_installer_reports_idempotent_component_actions():
    """Installer component branches should explain installs, skips, and no-ops."""
    source = (ROOT / "scripts" / "install_standalone.sh").read_text(encoding="utf-8")

    assert "Installed kitty-terminal-description CLI" in source
    assert "OpenRouter env file already exists" in source
    assert "Kitty remote control already enabled" in source


def test_core_cli_is_installed_unconditionally_by_main():
    """The CLI core must install independently of the component selection.

    ISC_PREFLIGHT alone is not enough: the shared runtime only fires the
    preflight in front of the *first selected* component, so a blank selection
    would skip it. main() therefore calls ensure_core_cli itself.
    """
    source = (ROOT / "scripts" / "install_standalone.sh").read_text(encoding="utf-8")

    assert "ensure_core_cli()" in source
    main_body = source.split("\nmain() {", 1)[1].split("\n}", 1)[0]
    assert "ensure_core_cli" in main_body
    assert main_body.index("ensure_core_cli") < main_body.index("component_main")


def test_blank_selection_still_installs_the_core_cli(tmp_path):
    """End-to-end: `--select ''` installs no component but yields a working core."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    env = {
        **os.environ,
        "TARGET_HOME": str(fake_home),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "ISC_HOTKEY_INTERACTIVE": "0",
    }
    result = subprocess.run(
        ["bash", str(ROOT / "install.sh"), "--select", ""],
        capture_output=True,
        text=True,
        env=env,
        stdin=subprocess.DEVNULL,
        timeout=300,
    )

    assert result.returncode == 0, result.stderr
    cli = fake_home / ".local" / "bin" / "kitty-terminal-description"
    assert cli.is_file(), result.stderr
    assert os.access(cli, os.X_OK)
    assert (fake_home / ".local" / "bin" / "logging_utils.py").is_file()
    # Nothing optional may be installed by a blank selection.
    assert not (fake_home / ".config" / "linux-kitty-terminal-description").exists()


def test_cli_is_not_an_optional_toggle():
    """The core CLI must not appear as a deselectable component."""
    manifest = (ROOT / "installer" / "components.sh").read_text(encoding="utf-8")

    # Optional, freely-togglable customizations only — no "cli|...|install_cli".
    assert "install_cli" not in manifest
    for required in ("env_file", "kitty_remote_control", "claude_hook", "codex_hook"):
        assert required in manifest
