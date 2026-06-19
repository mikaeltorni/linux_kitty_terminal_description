#!/usr/bin/env bash
# install_standalone.sh — Install Kitty terminal title generation hooks.
#
# Components:
#   - run_as_target(): Run user-level install commands for the target user.
#   - install_env_file(): Copy repository .env credentials to user config.
#   - configure_claude_hook(): Add a Claude UserPromptSubmit hook.
#   - main(): Install the CLI and connect detected Codex/Claude installations.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_USER="${SUDO_USER:-$USER}"
TARGET_UID="$(id -u "$TARGET_USER")"
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
RUNTIME_DIR="/run/user/${TARGET_UID}"
USER_BUS="unix:path=${RUNTIME_DIR}/bus"
DISPLAY_VAL="${DISPLAY:-:0}"

msg() { printf '\n==> %s\n' "$*"; }

run_as_target() {
  if [ "$(id -un)" = "$TARGET_USER" ]; then
    env \
      HOME="$TARGET_HOME" \
      USER="$TARGET_USER" \
      LOGNAME="$TARGET_USER" \
      XDG_RUNTIME_DIR="$RUNTIME_DIR" \
      DBUS_SESSION_BUS_ADDRESS="$USER_BUS" \
      DISPLAY="$DISPLAY_VAL" \
      "$@"
  else
    sudo -H -u "$TARGET_USER" env \
      HOME="$TARGET_HOME" \
      USER="$TARGET_USER" \
      LOGNAME="$TARGET_USER" \
      XDG_RUNTIME_DIR="$RUNTIME_DIR" \
      DBUS_SESSION_BUS_ADDRESS="$USER_BUS" \
      DISPLAY="$DISPLAY_VAL" \
      "$@"
  fi
}

install_env_file() {
  local source_env="$SCRIPT_DIR/.env"
  local config_dir="$TARGET_HOME/.config/linux-kitty-terminal-description"
  local target_env="$config_dir/openrouter.env"

  run_as_target mkdir -p "$config_dir"
  if [ -f "$source_env" ] && grep -Eq '^[[:space:]]*(export[[:space:]]+)?OPENROUTER_API_KEY=' "$source_env"; then
    run_as_target install -m 0600 "$source_env" "$target_env"
    msg "Installed OpenRouter env file at $target_env"
  elif [ ! -f "$target_env" ]; then
    run_as_target touch "$target_env"
    run_as_target chmod 0600 "$target_env"
    msg "Created empty env file at $target_env"
  fi
}

configure_kitty_remote_control() {
  local kitty_config_dir="$TARGET_HOME/.config/kitty"
  local kitty_config_file="$kitty_config_dir/kitty.conf"

  run_as_target mkdir -p "$kitty_config_dir"
  if [ ! -f "$kitty_config_file" ] || ! grep -Eq '^[[:space:]]*allow_remote_control[[:space:]]+yes([[:space:]]*(#.*)?)?$' "$kitty_config_file"; then
    run_as_target tee -a "$kitty_config_file" >/dev/null <<'EOF'

# Required for linux_kitty_terminal_description to keep prompt titles in Kitty tabs.
allow_remote_control yes
EOF
  fi
}

configure_claude_hook() {
  local claude_dir="$TARGET_HOME/.claude"
  local settings_file="$claude_dir/settings.json"
  local hook_command="kitty-terminal-description claude-hook"

  if [ ! -d "$claude_dir" ] && ! run_as_target bash -lc 'command -v claude >/dev/null 2>&1'; then
    msg "Claude Code not detected; skipping Claude hook"
    return 0
  fi

  run_as_target mkdir -p "$claude_dir"
  run_as_target env \
    CLAUDE_SETTINGS_FILE="$settings_file" \
    KITTY_DESCRIPTION_HOOK_COMMAND="$hook_command" \
    python3 - <<'PY'
import json
import os
from pathlib import Path

settings_file = Path(os.environ["CLAUDE_SETTINGS_FILE"])
hook_command = os.environ["KITTY_DESCRIPTION_HOOK_COMMAND"]

try:
    settings = json.loads(settings_file.read_text(encoding="utf-8"))
    if not isinstance(settings, dict):
        settings = {}
except Exception:
    settings = {}

hooks = settings.setdefault("hooks", {})
entries = hooks.setdefault("UserPromptSubmit", [])
if not isinstance(entries, list):
    entries = []
    hooks["UserPromptSubmit"] = entries

for entry in entries:
    for hook in entry.get("hooks", []):
        if hook.get("command") == hook_command:
            break
    else:
        continue
    break
else:
    for entry in entries:
        if entry.get("matcher", "") == "":
            entry.setdefault("hooks", []).append({"type": "command", "command": hook_command})
            break
    else:
        entries.append({"matcher": "", "hooks": [{"type": "command", "command": hook_command}]})

settings_file.parent.mkdir(parents=True, exist_ok=True)
settings_file.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
PY
  msg "Configured Claude UserPromptSubmit hook"
}

report_codex_detection() {
  if [ -d "$TARGET_HOME/.codex" ] || run_as_target bash -lc 'command -v codex >/dev/null 2>&1'; then
    msg "Codex detected; Agent Command Center will use kitty-terminal-description when installed"
  else
    msg "Codex not detected; skipping Codex hook"
  fi
}

main() {
  msg "Installing Linux Kitty Terminal Description for $TARGET_USER"

  local bin_dir="$TARGET_HOME/.local/bin"
  run_as_target mkdir -p "$bin_dir"
  run_as_target install -D -m 0755 "$SCRIPT_DIR/scripts/kitty_terminal_description.py" "$bin_dir/kitty-terminal-description"

  install_env_file
  configure_kitty_remote_control
  configure_claude_hook
  report_codex_detection

  msg "Linux Kitty Terminal Description installed. Ensure ~/.local/bin is on PATH."
}

main "$@"
