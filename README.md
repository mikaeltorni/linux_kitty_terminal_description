# Linux Kitty Terminal Description

Standalone Kitty terminal title generation for Codex and Claude prompts. It
summarizes a prompt with OpenRouter, includes project/Git context in the title,
and falls back to the raw prompt when the API key or network is unavailable.

This repository is also used by
[`agent_command_center`](https://github.com/mikaeltorni/agent_command_center).
When Agent Command Center is installed on the same machine, its installer copies
`OPENROUTER_API_KEY` from `agent_command_center/.env` into this tool's local env
file and uses `kitty-terminal-description` for prompt title summaries when the
command is available.

## Install

```bash
git clone https://github.com/mikaeltorni/linux_kitty_terminal_description.git
cd linux_kitty_terminal_description
printf 'OPENROUTER_API_KEY=...\n' > .env
bash install.sh
```

The installer is sudo-free. It installs `kitty-terminal-description` to
`~/.local/bin`, copies `.env` to
`~/.config/linux-kitty-terminal-description/openrouter.env` with mode `0600`,
enables Kitty remote control if `~/.config/kitty/kitty.conf` exists, and wires a
Claude `UserPromptSubmit` hook when `~/.claude` or `claude` exists.

Codex is detected but not force-wrapped by this standalone installer, because
Codex setups commonly already use Agent Command Center's notify wrapper. When
Agent Command Center is installed, Codex prompt titles are connected through
that tracker path.

## Usage

```bash
kitty-terminal-description describe --prompt "fix the flaky test" --project-path "$PWD"
kitty-terminal-description title --prompt "fix the flaky test" --project-path "$PWD"
kitty-terminal-description claude-hook < claude-hook-payload.json
```

Environment is read in this order:

1. `OPENROUTER_ENV_FILE`
2. `~/.config/linux-kitty-terminal-description/openrouter.env`
3. this repository's `.env`
4. `.env` next to the installed command

## Development

This project uses uv protections for Python dependency resolution:
`exclude-newer = "24 hours"` plus hash verification for `uv pip`.

```bash
uv sync --extra test
uv run pytest
bash -n install.sh scripts/install_standalone.sh
```

Do not use plain `pip install <package>` for dependency resolution.
