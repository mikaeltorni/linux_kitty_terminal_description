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

# msg - print a highlighted progress line to stdout. Args: $* message text.
msg() { printf '\n==> %s\n' "$*"; }

# run_as_target - run "$@" as the target user with a full desktop environment
# (HOME, DBUS, DISPLAY, XDG_RUNTIME_DIR). Uses sudo only when the current user
# differs from the target, keeping the installer sudo-free in the common case.
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

# install_env_file - copy the repository .env (mode 0600) into the user's
# config dir, or create an empty placeholder, without overwriting an existing
# key. Non-destructive and idempotent.
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

# configure_kitty_remote_control - append "allow_remote_control yes" to the
# user's kitty.conf once so the CLI can keep tab titles in sync. Idempotent.
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

# configure_claude_hook - wire the Claude UserPromptSubmit hook via
# claude_hook_config.py when Claude Code is present; skip silently otherwise.
configure_claude_hook() {
  local claude_dir="$TARGET_HOME/.claude"
  local settings_file="$claude_dir/settings.json"
  local hook_command="kitty-terminal-description claude-hook"

  if [ ! -d "$claude_dir" ] && ! run_as_target bash -lc 'command -v claude >/dev/null 2>&1'; then
    msg "Claude Code not detected; skipping Claude hook"
    return 0
  fi

  run_as_target mkdir -p "$claude_dir"
  run_as_target python3 "$SCRIPT_DIR/scripts/claude_hook_config.py" "$settings_file" "$hook_command"
  msg "Configured Claude UserPromptSubmit hook"
}

# configure_codex_hook - wire the Codex prompt-title summary path. Codex has no
# native prompt-submit hook, so its titles are summarized by the Agent Command
# Center tracker, which shells out to this repository's kitty-terminal-description
# CLI (the single source of OpenRouter summarization). This step confirms the
# wiring and reports whether Codex is present; the actual call happens at agent
# launch time inside the tracker.
configure_codex_hook() {
  if [ -d "$TARGET_HOME/.codex" ] || run_as_target bash -lc 'command -v codex >/dev/null 2>&1'; then
    msg "Codex detected; prompt titles use kitty-terminal-description via the Agent Command Center tracker"
  else
    msg "Codex not detected; skipping Codex prompt-title hook"
  fi
}

# install_cli - install the kitty-terminal-description CLI into the target
# user's ~/.local/bin.
install_cli() {
  local bin_dir="$TARGET_HOME/.local/bin"
  run_as_target mkdir -p "$bin_dir"
  run_as_target install -D -m 0755 "$SCRIPT_DIR/scripts/kitty_terminal_description.py" "$bin_dir/kitty-terminal-description"
}

# Component selection runtime and this repository's component manifest.
# Load the shared installer component framework. Its single source of truth is
# the linux_installation_scripts_functions repository (cloned as a sibling by
# installation_scripts, or downloaded on demand) -- no per-repo vendored copy.
for __isc_d in "${ISC_FUNCTIONS_DIR:-}" \
               "$SCRIPT_DIR/../linux_installation_scripts_functions" \
               "$HOME/projects/linux_installation_scripts_functions"; do
  [ -n "$__isc_d" ] && [ -f "$__isc_d/component_loader.sh" ] && { source "$__isc_d/component_loader.sh"; break; }
done
declare -F isc_activate_components >/dev/null 2>&1 || \
  source <(curl -fsSL "https://raw.githubusercontent.com/mikaeltorni/linux_installation_scripts_functions/${ISC_FUNCTIONS_REF:-master}/component_loader.sh")
# Shared install-time helpers (msg, run_as_target) from the same framework
# checkout, before main()/component_main runs any component.
isc_load_helpers
isc_activate_components
source "$SCRIPT_DIR/installer/components.sh"

# main - run the full install, delegating component selection/execution to the
# shared component_main runtime. Args: forwarded component flags ("$@").
main() {
  msg "Installing Linux Kitty Terminal Description for $TARGET_USER"
  component_main "$@"
  msg "Linux Kitty Terminal Description installed. Ensure ~/.local/bin is on PATH."
}

# Listing/help must print only their own output (the master installer parses
# --list-components); bypass the surrounding messages for those.
case "${1:-}" in
  --list-components|--help|-h) component_main "$@"; exit $? ;;
esac

main "$@"
