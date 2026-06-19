#!/usr/bin/env bash
# components.sh - Component manifest for linux_kitty_terminal_description.
#
# Maps each Kitty-integration component to the function (defined in
# scripts/install_standalone.sh) that installs it. The standalone installer
# defines its functions, sources this manifest, then routes execution through
# component_main from component_runtime.sh.
#
# Entry format: "id|label|default(on/off)|function".

ISC_REPO_NAME="linux_kitty_terminal_description"
ISC_REPO_LABEL="Kitty prompt-title summaries"

ISC_COMPONENTS=(
  "cli|kitty-terminal-description CLI|on|install_cli"
  "env_file|OpenRouter API key env file|on|install_env_file"
  "kitty_remote_control|Enable Kitty remote control|on|configure_kitty_remote_control"
  "claude_hook|Claude Code UserPromptSubmit hook|on|configure_claude_hook"
  "codex_detection|Codex detection notice|on|report_codex_detection"
)
