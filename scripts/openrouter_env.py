#!/usr/bin/env python3
"""OpenRouter env-file helper for linux_kitty_terminal_description.

This repository owns the OpenRouter API key file it reads at summarization time
(``~/.config/linux-kitty-terminal-description/openrouter.env``). Consumers such
as Agent Command Center point the installer at a source ``.env`` that may hold
unrelated secrets; this helper copies only ``OPENROUTER_API_KEY`` so nothing
else leaks into the Kitty config.

Components:
  - copy_openrouter_key: Sync only OPENROUTER_API_KEY between env files.
  - write_openrouter_key: Store a directly entered key without logging it.
  - main: ``copy-key SOURCE TARGET`` CLI used by scripts/install_standalone.sh.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from logging_utils import get_logger, log_call

logger = get_logger("openrouter_env", "events.log", stream=True)


def _env_key(line: str) -> str:
    """Return the normalized variable name from one env-file line."""
    stripped = line.strip()
    key = stripped.split("=", 1)[0].strip() if "=" in stripped else ""
    if key.startswith("export "):
        key = key[len("export ") :].strip()
    return key


@log_call(logger)
def copy_openrouter_key(source_env: Path, target_env: Path) -> bool:
    """Copy only OPENROUTER_API_KEY from ``source_env`` to ``target_env``.

    Unrelated variables already present in ``target_env`` are preserved, and the
    target is written with 0600 permissions. Returns True when a key was copied.
    """
    if not source_env.is_file():
        logger.info("OpenRouter source env file does not exist: %s", source_env)
        return False

    key_line = ""
    for raw in source_env.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        if _env_key(stripped) == "OPENROUTER_API_KEY":
            key_line = stripped
            break

    if not key_line:
        logger.info("OPENROUTER_API_KEY was not present in %s", source_env)
        return False

    lines = []
    if target_env.exists():
        for raw in target_env.read_text(encoding="utf-8").splitlines():
            if _env_key(raw.strip()) != "OPENROUTER_API_KEY":
                lines.append(raw)
    lines.append(key_line)
    target_env.parent.mkdir(parents=True, exist_ok=True)
    target_env.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    target_env.chmod(0o600)
    logger.info("Copied OPENROUTER_API_KEY into %s", target_env)
    return True


def write_openrouter_key(target_env: Path, key: str) -> bool:
    """Store a directly entered OpenRouter key privately without logging it.

    The key is deliberately not passed through the call-tracing decorator. A
    newline is rejected so one input value cannot inject additional env rows.
    """
    if not key or "\n" in key or "\r" in key:
        logger.warning("Refusing empty or multiline OPENROUTER_API_KEY input")
        return False

    lines = []
    if target_env.exists():
        lines = [
            raw
            for raw in target_env.read_text(encoding="utf-8").splitlines()
            if _env_key(raw.strip()) != "OPENROUTER_API_KEY"
        ]
    lines.append(f"OPENROUTER_API_KEY={key}")
    target_env.parent.mkdir(parents=True, exist_ok=True)
    target_env.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    target_env.chmod(0o600)
    logger.info("Stored directly entered OPENROUTER_API_KEY in %s", target_env)
    return True


def build_parser() -> argparse.ArgumentParser:
    """Create the OpenRouter env CLI parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    copy_key = subcommands.add_parser("copy-key")
    copy_key.add_argument("source_env", type=Path)
    copy_key.add_argument("target_env", type=Path)
    set_key = subcommands.add_parser("set-key")
    set_key.add_argument("target_env", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one OpenRouter env operation."""
    args = build_parser().parse_args(argv)
    if args.command == "copy-key":
        copy_openrouter_key(args.source_env, args.target_env)
    elif args.command == "set-key":
        if not write_openrouter_key(args.target_env, sys.stdin.read().rstrip("\n")):
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
