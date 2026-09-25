# Clean Installation Compatibility

The `linux-configuration` skill owns the clean-install and root-optional
installer rules; they are not restated here. Repository-specific installer
facts:

The tool must still function when the OpenRouter key or network is
unavailable, by falling back to the raw prompt.

# Ownership: OpenRouter prompt and commit generation is single-sourced here

This repository is the **single source of truth** for OpenRouter prompt
summarization and validated Qwen conventional-commit subject generation across
the desktop setup, exposed through the `kitty-terminal-description` CLI. The
summarizer used to be duplicated inside `agent_command_center`; it lives here
only. Do not re-add a second OpenRouter text client to Agent Command Center,
Phrase Automation, or another consumer — they shell out to this CLI and reuse
its `OPENROUTER_API_KEY` lookup. When you change prompt/commit generation,
model choice, or title/context formatting, make the change here.

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
