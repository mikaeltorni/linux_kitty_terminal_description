#!/usr/bin/env bash
# components.sh - Component manifest for linux_kitty_terminal_description.
#
# Maps each Kitty-integration component to the function (defined in
# scripts/install_standalone.sh) that installs it. The standalone installer
# defines its functions, sources this manifest, then routes execution through
# component_main from component_runtime.sh.
#
# Entry format: "id|label|default(on/off)|function".
#
# The CLI itself is the program's non-optional core and is not listed here: it is
# installed unconditionally by the ensure_core_cli preflight (see
# install_standalone.sh) before any component below runs. Every entry here is an
# optional customization the user can freely toggle; the core keeps working no
# matter which ones are selected or deselected.

ISC_REPO_NAME="linux_kitty_terminal_description"
ISC_REPO_LABEL="Kitty prompt-title summaries"

ISC_COMPONENTS=(
  "env_file|Configure OpenRouter API key|on|install_env_file|detect_env_file|uninstall_env_file"
  "kitty_remote_control|Enable Kitty remote control|on|configure_kitty_remote_control|detect_kitty_remote_control|uninstall_kitty_remote_control"
  "claude_hook|Claude Code UserPromptSubmit hook|on|configure_claude_hook|detect_claude_hook|uninstall_claude_hook"
  "codex_hook|Codex prompt-title hook (via Agent Command Center tracker)|on|configure_codex_hook|detect_codex_hook"
)
