"""claude_hook_config.py - install-time Claude Code hook settings helper.

Components:
  - configure_user_prompt_submit_hook: Add the Kitty title hook idempotently.
  - main: CLI entrypoint used by install_standalone.sh.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from logging_utils import get_logger, log_call

logger = get_logger("claude_hook_config", "events.log", stream=True)


def _read_settings(settings_file: Path) -> dict[str, Any]:
    """Read a Claude settings JSON file defensively.

    Args:
        settings_file (Path): Path to the ``settings.json`` file.

    Returns:
        dict[str, Any]: The parsed settings object, or an empty dict when the
        file is missing, unreadable, or not a JSON object.
    """
    try:
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
        if isinstance(settings, dict):
            logger.debug("Read Claude settings from %s", settings_file)
            return settings
        logger.warning("Ignoring non-object Claude settings file: %s", settings_file)
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("Could not read Claude settings from %s: %s", settings_file, exc)
    return {}


def _entry_has_command(entry: Any, command: str) -> bool:
    """Check whether a single hook matcher entry already runs a command.

    Args:
        entry (Any): One ``UserPromptSubmit`` matcher entry (expected dict).
        command (str): The hook command string to look for.

    Returns:
        bool: ``True`` when ``entry`` contains a hook running ``command``.
    """
    if not isinstance(entry, dict):
        return False
    hooks = entry.get("hooks", [])
    if not isinstance(hooks, list):
        return False
    return any(isinstance(hook, dict) and hook.get("command") == command for hook in hooks)


@log_call(logger)
def configure_user_prompt_submit_hook(settings_file: Path, hook_command: str) -> None:
    """Ensure the Claude ``UserPromptSubmit`` hook runs ``hook_command`` once.

    Idempotent and non-destructive: existing hooks and unrelated settings are
    preserved, the command is appended to the empty-matcher entry when present
    (otherwise a new matcher entry is created), and re-running makes no further
    change. Malformed ``hooks`` containers are repaired in place.

    Args:
        settings_file (Path): Path to the Claude ``settings.json`` to update;
            parent directories are created as needed.
        hook_command (str): The command to register under ``UserPromptSubmit``.

    Returns:
        None
    """
    settings = _read_settings(settings_file)
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}
        settings["hooks"] = hooks

    entries = hooks.setdefault("UserPromptSubmit", [])
    if not isinstance(entries, list):
        entries = []
        hooks["UserPromptSubmit"] = entries

    if not any(_entry_has_command(entry, hook_command) for entry in entries):
        for entry in entries:
            if isinstance(entry, dict) and entry.get("matcher", "") == "":
                entry.setdefault("hooks", []).append({"type": "command", "command": hook_command})
                break
        else:
            entries.append({"matcher": "", "hooks": [{"type": "command", "command": hook_command}]})

    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    logger.info("Configured Claude UserPromptSubmit hook in %s", settings_file)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint used by ``install_standalone.sh``.

    Args:
        argv (list[str] | None): Argument vector ``[settings_file, hook_command]``;
            defaults to ``sys.argv[1:]``.

    Returns:
        int: ``0`` on success, ``2`` on incorrect usage.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("Usage: claude_hook_config.py <settings.json> <hook-command>", file=sys.stderr)
        logger.warning("Invalid arguments: expected 2, received %d", len(args))
        return 2
    configure_user_prompt_submit_hook(Path(args[0]), args[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
