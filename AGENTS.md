# Clean Installation Compatibility

This repository is part of the `installation_scripts` Linux setup chain. Keep
the standalone, sudo-free `install.sh` idempotent and compatible with a clean
checkout. Every runtime dependency, wrapper, hook, and default the feature needs
must be represented in the installer, and the tool must still function when the
OpenRouter key or network is unavailable by falling back to the raw prompt.

# Ownership: OpenRouter summarization is single-sourced here

This repository is the **single source of truth** for OpenRouter prompt
summarization across the desktop setup, exposed through the
`kitty-terminal-description` CLI. The summarizer used to be duplicated inside
`agent_command_center`; it lives here only. Do not re-add a second summarizer to
Agent Command Center or any other consumer — its tracker shells out to this
CLI for every prompt-title and finish-label summary and supplies the
`OPENROUTER_API_KEY`. When you change summarization behavior, model choice, or
title/context formatting, make the change here.

# Repository Independence

The Claude Code `UserPromptSubmit` hook and the CLI work standalone without
Agent Command Center. The framework dependency
(`linux_installation_scripts_functions`) is soft — resolved from a sibling
checkout or downloaded on demand. Keep it that way; do not introduce a hard
requirement on ACC or any sibling.

# Secret handling

The OpenRouter API key is prompted with hidden input, stored at
`~/.config/linux-kitty-terminal-description/openrouter.env` with mode `0600`,
and must never be logged. Preserve this when touching the installer or hooks.
