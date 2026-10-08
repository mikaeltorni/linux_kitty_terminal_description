# Linux Kitty Terminal Description

> Part of [installation_scripts](https://github.com/mikaeltorni/installation_scripts) — generates **Kitty** terminal titles by summarizing Codex/Claude prompts via **OpenRouter**. Tested on Ubuntu 24.04.4 LTS; the Python summarizer is OS-portable (Kitty runs on Linux/macOS/Windows; install paths untested off Ubuntu).

Standalone Kitty terminal title generation for Codex and Claude prompts. It
summarizes a prompt with OpenRouter, includes project/Git context in the title,
and falls back to the raw prompt when the API key or network is unavailable.

This repository is the **single source of truth** for OpenRouter prompt
summarization across the desktop setup. The summarizer used to be duplicated
inside [`agent_command_center`](https://github.com/mikaeltorni/agent_command_center);
it now lives here only, exposed through the `kitty-terminal-description` CLI and
installed into the Claude Code and Codex agent hooks. When Agent Command Center
is installed on the same machine, its installer copies `OPENROUTER_API_KEY` from
`agent_command_center/.env` into this tool's local env file, and its tracker
shells out to `kitty-terminal-description` for every prompt-title and
finish-announcement summary (degrading to the raw-prompt title when the command
is absent — it keeps no summarizer of its own).

## Repository dependencies

This repository installs and runs **standalone** — it summarizes prompts on its
own and degrades to the raw prompt when the API key or network is unavailable.

- **Build-time (soft):** the shared installer component framework
  [`linux_installation_scripts_functions`](https://github.com/mikaeltorni/linux_installation_scripts_functions) —
  loaded from a sibling checkout when present, otherwise downloaded on demand.
- **Consumer (Agent Command Center):** when
  [`agent_command_center`](https://github.com/mikaeltorni/agent_command_center)
  is installed on the same machine, *its* installer copies `OPENROUTER_API_KEY`
  into this tool's env file, and its tracker calls `kitty-terminal-description`
  for all prompt-title and finish-label summaries (this repo owns the summarizer;
  ACC no longer bundles its own). This repository does not require ACC to
  function — the Claude Code hook works on its own.

See the full cross-repository map in
[installation_scripts/DEPENDENCIES.md](https://github.com/mikaeltorni/installation_scripts/blob/master/DEPENDENCIES.md).

## Install

```bash
git clone https://github.com/mikaeltorni/linux_kitty_terminal_description.git
cd linux_kitty_terminal_description
bash install.sh
```

The installer is sudo-free. If no key exists in `.env`, it securely prompts for
the OpenRouter API key with hidden input; the key value is never logged. It
installs `kitty-terminal-description` to `~/.local/bin`, copies the key to
`~/.config/linux-kitty-terminal-description/openrouter.env` with mode `0600`,
enables Kitty remote control if `~/.config/kitty/kitty.conf` exists, and wires a
Claude `UserPromptSubmit` hook when `~/.claude` or `claude` exists.

Codex is detected but not force-wrapped by this standalone installer, because
Codex setups commonly already use Agent Command Center's notify wrapper. When
Agent Command Center is installed, Codex prompt titles are connected through
that tracker path.

## Usage

The installed `kitty-terminal-description` command is a Rust binary when cargo
is available. It accepts `describe`, `title`, `completion-message`,
`commit-message`, `generate-json`, `claude-hook`, `clean-label`,
`env-copy-key`, `env-set-key`, `hook-config`, and `hook-remove`. Without an
OpenRouter key, `describe` prints the raw prompt title and does not call the
network. The installer uses `env-copy-key`, `env-set-key`, and `hook-config`
to write the key file and the Claude hook.

```bash
kitty-terminal-description describe --prompt "fix the flaky test" --project-path "$PWD"
kitty-terminal-description title --prompt "fix the flaky test" --project-path "$PWD"
git diff HEAD | kitty-terminal-description commit-message --project-path "$PWD"
printf '%s' '{"system_prompt":"Return Markdown.","user_prompt":"Project context","model":"google/gemini-2.5-flash-lite","max_tokens":65536,"temperature":0}' | kitty-terminal-description generate-json
kitty-terminal-description claude-hook < claude-hook-payload.json
```

`commit-message` uses the same private OpenRouter key lookup as title summaries
and defaults to `google/gemini-2.5-flash-lite`. It reads a unified diff from stdin,
sends it to OpenRouter, and prints exactly one validated Conventional Commits
subject. Missing credentials, provider errors, empty diffs, and invalid model
output return a non-zero status and print no commit subject.

`generate-json` is the machine-readable generic generation boundary used by
Phrase Automation project prompts. It accepts one JSON request on stdin and
prints `{status,text,detail}` JSON on stdout. It does not truncate the supplied
prompts; its default output ceiling is 65,536 tokens, temperature is zero, and
`OPENROUTER_GENERATE_TIMEOUT` controls its long-request timeout (600 seconds by
default). Provider failures are returned as structured statuses.

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

## Component selection

This repository's `install.sh` participates in the shared installer
component menu (see the [`installation_scripts`](https://github.com/mikaeltorni/installation_scripts)
master installer). Run standalone, it offers an interactive submenu on a
terminal; the master installer drives it non-interactively.

```bash
bash install.sh                 # interactive component selection (TTY), else defaults
bash install.sh --default       # install all default-on components, no prompts
bash install.sh --all           # install every component, no prompts
bash install.sh --select a,b    # install exactly these component ids
bash install.sh --list-components  # print: id<TAB>label<TAB>default
```

The `kitty-terminal-description` CLI is the program's **core** and is always
installed before any component runs — you cannot deselect it, so the core keeps
working no matter what you choose. Everything else is an optional customization
you can freely toggle.

In the interactive menu, **space** toggles a component, `a`/`n` select all
or none, and **Enter** installs the selection. Optional components:

| Component id | Description | Default |
|---|---|---|
| `env_file` | Configure OpenRouter API key | on |
| `kitty_remote_control` | Enable Kitty remote control | on |
| `claude_hook` | Claude Code UserPromptSubmit hook | on |
| `codex_hook` | Codex prompt-title hook (via Agent Command Center tracker) | on |

Runtime behaviour is further tunable through environment variables read by the
CLI: `OPENROUTER_SUMMARY_MODEL`, `OPENROUTER_SUMMARY_TIMEOUT`,
`OPENROUTER_SUMMARY_CACHE_TTL`, `OPENROUTER_SUMMARY_CACHE_DIR`, and
`OPENROUTER_ENV_FILE`. `OPENROUTER_GENERATE_TIMEOUT` controls generic long-form
requests. `OPENROUTER_COMMIT_MODEL` optionally overrides the Qwen
model for commit subjects without changing title summaries.


## Detect, reconfigure, and uninstall

This installer tracks what it has installed and can re-apply or remove it, so you
can refresh configuration after a repo update or cleanly back a feature out.

```bash
bash install.sh --detect            # show each component as installed|absent
bash install.sh --reconfigure a,b   # re-apply (idempotent) these component ids
bash install.sh --uninstall a,b     # uninstall these component ids
```

In the interactive menu (run `bash install.sh` on a terminal, or via the master
installer), already-installed components show a green `✓`. Select one with
**space** to **reconfigure** it (`~`), press **`u`** to mark it for **uninstall**
(`✗`), or press **`r`** to reconfigure every installed component at once. Detection
uses a live check where deterministic and otherwise an install receipt under
`${XDG_STATE_HOME:-~/.local/state}/isc/receipts/`; a component without a reversal
step simply clears that receipt on uninstall.

## Disclaimer

This software is provided under the MIT License on an **“as is”** basis, without warranties of any kind. To the maximum extent permitted by applicable law, the authors and copyright holders shall not be liable for any claims, damages, losses, or other liability arising from the use of this software.

You are solely responsible for determining whether this software is suitable, safe, lawful, and appropriate for your intended use. Unless explicitly stated otherwise, this project is general-purpose software and is not designed, tested, certified, or approved for safety-critical, medical, automotive, aviation, industrial-control, life-support, cybersecurity-critical, financial-critical, or other high-risk use cases.

The authors and copyright holders make no guarantees regarding security, reliability, availability, correctness, compliance, non-infringement, or fitness for any particular purpose.

This notice is intended to clarify the nature of the project and does not impose additional restrictions beyond the MIT License.
