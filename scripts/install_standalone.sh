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

# The shared install-time helpers (msg, run_as_target) come from the framework's
# installer_helpers.sh, loaded via isc_load_helpers below before main() runs any
# component. No vendored copy here.

# install_env_file - copy only OPENROUTER_API_KEY from the source env into the
# user's config dir (mode 0600), securely prompt for one when interactive, or
# create an empty placeholder when unattended. The source
# defaults to this repository's own .env but can be overridden with
# KITTY_TERMINAL_DESCRIPTION_SOURCE_ENV so a consumer (e.g. Agent Command Center)
# can hand over its .env without leaking unrelated secrets. Non-destructive and
# idempotent.
install_env_file() {
  local source_env="${KITTY_TERMINAL_DESCRIPTION_SOURCE_ENV:-$SCRIPT_DIR/.env}"
  local config_dir="$TARGET_HOME/.config/linux-kitty-terminal-description"
  local target_env="$config_dir/openrouter.env"

  run_as_target mkdir -p "$config_dir"
  if [ -f "$source_env" ] && grep -Eq '^[[:space:]]*(export[[:space:]]+)?OPENROUTER_API_KEY=' "$source_env"; then
    run_as_target python3 "$SCRIPT_DIR/scripts/openrouter_env.py" copy-key "$source_env" "$target_env"
    msg "Installed OpenRouter key at $target_env"
  elif [ -f "$target_env" ] && grep -Eq '^[[:space:]]*(export[[:space:]]+)?OPENROUTER_API_KEY=.+$' "$target_env"; then
    msg "OpenRouter key is already configured at $target_env; leaving it unchanged"
    return 0
  elif [ -t 0 ]; then
    local openrouter_key=""
    read -r -s -p "OpenRouter API key (leave blank to configure later): " openrouter_key
    printf '\n' >&2
    if [ -n "$openrouter_key" ]; then
      printf '%s' "$openrouter_key" | run_as_target python3 "$SCRIPT_DIR/scripts/openrouter_env.py" set-key "$target_env"
      msg "Installed OpenRouter key at $target_env"
      unset openrouter_key
      return 0
    fi
    unset openrouter_key
  fi

  if [ ! -f "$target_env" ]; then
    run_as_target touch "$target_env"
    run_as_target chmod 0600 "$target_env"
    msg "Created empty env file at $target_env"
  else
    msg "OpenRouter env file already exists at $target_env; leaving it unchanged"
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
    msg "Enabled Kitty remote control in $kitty_config_file"
  else
    msg "Kitty remote control already enabled in $kitty_config_file"
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
# user's ~/.local/bin. This is the program's core: every other component (the
# Claude/Codex hooks, the Agent Command Center tracker) shells out to it. It is
# installed unconditionally via ensure_core_cli (the component preflight) before
# any optional component runs, so a user can deselect every customization and the
# core still works. The __core_cli_installed guard collapses the preflight and
# any direct call into a single install per run.
__core_cli_installed=0
install_cli() {
  if [ "$__core_cli_installed" = "1" ]; then
    return 0
  fi
  local bin_dir="$TARGET_HOME/.local/bin"
  run_as_target mkdir -p "$bin_dir"
  run_as_target install -D -m 0755 "$SCRIPT_DIR/scripts/kitty_terminal_description.py" "$bin_dir/kitty-terminal-description"
  run_as_target install -D -m 0644 "$SCRIPT_DIR/scripts/logging_utils.py" "$bin_dir/logging_utils.py"
  __core_cli_installed=1
  msg "Installed kitty-terminal-description CLI at $bin_dir/kitty-terminal-description"
}

# ensure_core_cli - component preflight that guarantees the core CLI is present
# regardless of which optional components were selected, so the hook components
# never wire to a missing kitty-terminal-description command.
ensure_core_cli() {
  install_cli
}

###############################################################################
# Detection and uninstall (component lifecycle)
###############################################################################

# detect_env_file - true when the OpenRouter env file has been installed.
detect_env_file() {
  [ -f "$TARGET_HOME/.config/linux-kitty-terminal-description/openrouter.env" ]
}

# detect_kitty_remote_control - true when kitty.conf enables remote control.
detect_kitty_remote_control() {
  local f="$TARGET_HOME/.config/kitty/kitty.conf"
  [ -f "$f" ] && grep -Eq '^[[:space:]]*allow_remote_control[[:space:]]+yes' "$f"
}

# detect_claude_hook - true when the Claude settings reference our hook command.
detect_claude_hook() {
  local f="$TARGET_HOME/.claude/settings.json"
  [ -f "$f" ] && grep -q 'kitty-terminal-description claude-hook' "$f"
}

# detect_codex_hook - true when Codex is present, mirroring configure_codex_hook.
# The Codex prompt-title path has no persistent artifact of its own (the summary
# call happens at agent launch inside the Agent Command Center tracker), so the
# component is "installed" exactly when Codex is available for the tracker to
# drive. Without a detect function this component could never report installed on
# a clean machine, since the runtime has no receipt to fall back on.
detect_codex_hook() {
  [ -d "$TARGET_HOME/.codex" ] || run_as_target bash -lc 'command -v codex >/dev/null 2>&1'
}

# uninstall_env_file - remove the installed OpenRouter env file and its config dir.
uninstall_env_file() {
  local config_dir="$TARGET_HOME/.config/linux-kitty-terminal-description"
  msg "Removing OpenRouter env file in $config_dir"
  run_as_target rm -f "$config_dir/openrouter.env"
  run_as_target rmdir "$config_dir" 2>/dev/null || true
}

# uninstall_kitty_remote_control - drop the allow_remote_control line (and the
# preceding marker comment) this installer appended to kitty.conf.
uninstall_kitty_remote_control() {
  local f="$TARGET_HOME/.config/kitty/kitty.conf"
  [ -f "$f" ] || return 0
  msg "Disabling Kitty remote control in $f"
  run_as_target python3 - "$f" <<'PY'
import sys, io
path = sys.argv[1]
with io.open(path, encoding="utf-8") as fh:
    lines = fh.readlines()
out, skip_next_blank = [], False
marker = "linux_kitty_terminal_description to keep prompt titles"
kept = []
for ln in lines:
    if "allow_remote_control" in ln and "yes" in ln:
        continue
    if marker in ln:
        continue
    kept.append(ln)
# collapse the trailing blank line left by the removed block
while len(kept) >= 2 and kept[-1].strip() == "" and kept[-2].strip() == "":
    kept.pop()
with io.open(path, "w", encoding="utf-8") as fh:
    fh.writelines(kept)
PY
}

# uninstall_claude_hook - remove our UserPromptSubmit hook from Claude settings.
uninstall_claude_hook() {
  local f="$TARGET_HOME/.claude/settings.json"
  [ -f "$f" ] || return 0
  msg "Removing Claude UserPromptSubmit kitty-title hook from $f"
  run_as_target python3 - "$f" <<'PY'
import json, sys
path = sys.argv[1]
try:
    with open(path) as fh:
        data = json.load(fh)
except Exception:
    sys.exit(0)
hooks = data.get("hooks", {})
ups = hooks.get("UserPromptSubmit")
def keep(entry):
    s = json.dumps(entry)
    return "kitty-terminal-description claude-hook" not in s
if isinstance(ups, list):
    ups = [e for e in ups if keep(e)]
    if ups:
        hooks["UserPromptSubmit"] = ups
    else:
        hooks.pop("UserPromptSubmit", None)
    if hooks:
        data["hooks"] = hooks
    else:
        data.pop("hooks", None)
    with open(path, "w") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
PY
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

# Always install the core CLI before any selected component runs. The shared
# runtime invokes ISC_PREFLIGHT once, just before the first component, so the
# core is guaranteed whenever the user installs anything at all.
ISC_PREFLIGHT="ensure_core_cli"

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
  --list-components|--export-selection|--detect|--help|-h|--uninstall|--uninstall=*) component_main "$@"; exit $? ;;
esac

main "$@"
