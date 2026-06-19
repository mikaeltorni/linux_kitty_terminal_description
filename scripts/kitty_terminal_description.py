#!/usr/bin/env python3
"""kitty_terminal_description.py — Prompt summaries for Kitty terminal titles.

Components:
  - OpenRouter summary helpers for short prompt labels.
  - Project/Git context helpers for readable terminal titles.
  - Feature-label cache shared by title and completion-message callers.
  - CLI subcommands: describe, title, completion-message, and claude-hook.

Usage:
  kitty-terminal-description describe --prompt "fix tests" --project-path "$PWD"
  kitty-terminal-description title --prompt "fix tests" --project-path "$PWD"
  kitty-terminal-description claude-hook < payload.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple
from urllib import error, request


DEFAULT_SUMMARY_MODEL = "qwen/qwen3.5-flash-02-23"
DEFAULT_SUMMARY_CACHE_TTL_SECONDS = 6 * 60 * 60
FEATURE_LABEL_TTL_SECONDS = 2 * 60 * 60
OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
HTTP_REFERER = "https://github.com/mikaeltorni/linux_kitty_terminal_description"
X_TITLE = "Linux Kitty Terminal Description"
SYSTEM_PROMPT = (
    "You compress a software task description into a short feature label of "
    "two to five words. Output only the label in plain words. No punctuation, "
    "no quotes, no trailing period."
)

STATUS_OK = "ok"
STATUS_RATE_LIMITED = "rate_limited"
STATUS_OUT_OF_CREDITS = "out_of_credits"
STATUS_ERROR = "error"
STATUS_SKIPPED = "skipped"
STATUS_NO_KEY = "no_key"

STATE_DIR = Path(
    os.environ.get(
        "KITTY_TERMINAL_DESCRIPTION_STATE_DIR",
        Path.home() / ".local" / "state" / "linux-kitty-terminal-description",
    )
)
LOG_DIR = Path(
    os.environ.get(
        "KITTY_TERMINAL_DESCRIPTION_LOG_DIR",
        Path.home() / "projects" / "linux_kitty_terminal_description" / ".log",
    )
)
LOG_FILE = LOG_DIR / "events.log"


class SummaryResult(NamedTuple):
    """Outcome of a prompt-summary attempt."""

    status: str
    label: str = ""
    detail: str = ""


def append_log(message: str) -> None:
    """Append a best-effort repository-local log line."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        LOG_FILE.open("a", encoding="utf-8").write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {message}\n")
    except OSError:
        pass


def summary_model() -> str:
    """Return the configured OpenRouter summary model id."""
    return (os.environ.get("OPENROUTER_SUMMARY_MODEL") or "").strip() or DEFAULT_SUMMARY_MODEL


def request_timeout() -> float:
    """Return the OpenRouter request timeout in seconds."""
    raw = (os.environ.get("OPENROUTER_SUMMARY_TIMEOUT") or "").strip()
    try:
        value = float(raw)
    except ValueError:
        return 8.0
    return value if value > 0 else 8.0


def env_file_candidates() -> list[Path]:
    """Return candidate env files, most specific first."""
    candidates: list[Path] = []
    override = (os.environ.get("OPENROUTER_ENV_FILE") or "").strip()
    if override:
        candidates.append(Path(override).expanduser())
    config_home = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    candidates.append(Path(config_home) / "linux-kitty-terminal-description" / "openrouter.env")
    here = Path(__file__).resolve()
    candidates.append(here.parent.parent / ".env")
    candidates.append(here.parent / ".env")
    return candidates


def load_dotenv(path: Path | None = None) -> None:
    """Load KEY=VALUE pairs from the first existing env file."""
    paths = [path] if path is not None else env_file_candidates()
    for env_path in paths:
        try:
            text = env_path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            continue
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key.startswith("export "):
                key = key[len("export "):].strip()
            if key and key not in os.environ:
                os.environ[key] = value.strip().strip('"').strip("'")
        return


def api_key() -> str:
    """Return the OpenRouter API key, loading env files if needed."""
    key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if key:
        return key
    load_dotenv()
    return (os.environ.get("OPENROUTER_API_KEY") or "").strip()


def summary_cache_ttl() -> float:
    """Return the prompt-summary cache TTL in seconds."""
    raw = (os.environ.get("OPENROUTER_SUMMARY_CACHE_TTL") or "").strip()
    if not raw:
        return float(DEFAULT_SUMMARY_CACHE_TTL_SECONDS)
    try:
        return float(raw)
    except ValueError:
        return float(DEFAULT_SUMMARY_CACHE_TTL_SECONDS)


def summary_cache_dir() -> Path:
    """Return the OpenRouter label cache directory."""
    override = (os.environ.get("OPENROUTER_SUMMARY_CACHE_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    return STATE_DIR / "summary-cache"


def _summary_cache_path(model: str, prompt: str) -> Path:
    digest = hashlib.sha256(f"{model}\n{prompt}".encode("utf-8")).hexdigest()
    return summary_cache_dir() / f"{digest}.txt"


def read_cached_label(model: str, prompt: str) -> str | None:
    """Return a fresh cached label, or None."""
    ttl = summary_cache_ttl()
    if ttl <= 0:
        return None
    try:
        path = _summary_cache_path(model, prompt)
        if not path.is_file() or (time.time() - path.stat().st_mtime) > ttl:
            return None
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def write_cached_label(model: str, prompt: str, label: str) -> None:
    """Persist a prompt label for paired title/completion calls."""
    if not label or summary_cache_ttl() <= 0:
        return
    try:
        path = _summary_cache_path(model, prompt)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(label, encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def clean_label(text: str, *, max_words: int = 8, max_chars: int = 64) -> str:
    """Normalize a model response into a safe, terse feature label."""
    if not text:
        return ""
    text = re.sub(r"<think>.*?</think>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<think>.*$", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"</?think>", " ", text, flags=re.IGNORECASE)
    first_line = text.strip().splitlines()[0] if text.strip().splitlines() else ""
    stripped = first_line.strip().strip('"').strip("'").strip()
    stripped = re.sub(r"[\"'`*_#]", "", stripped)
    stripped = re.sub(r"[.;:,]+$", "", stripped)
    words = stripped.split()
    if max_words > 0:
        words = words[:max_words]
    label = " ".join(words).strip()
    if len(label) > max_chars:
        label = label[:max_chars].rsplit(" ", 1)[0].strip() or label[:max_chars].strip()
    return label


def _classify_http_error(code: int, body: str) -> str:
    lowered = (body or "").lower()
    if code == 429 or "rate limit" in lowered or "rate-limit" in lowered:
        return STATUS_RATE_LIMITED
    if code == 402 or "insufficient" in lowered or "out of credit" in lowered or "credits" in lowered or "quota" in lowered:
        return STATUS_OUT_OF_CREDITS
    return STATUS_ERROR


def summarize_prompt(prompt: str, *, model: str | None = None, key: str | None = None) -> SummaryResult:
    """Compress a prompt into a short OpenRouter feature label."""
    cleaned_prompt = " ".join((prompt or "").split())
    if not cleaned_prompt:
        return SummaryResult(STATUS_SKIPPED, "", "empty prompt")
    resolved_model = model or summary_model()
    cached_label = read_cached_label(resolved_model, cleaned_prompt)
    if cached_label:
        return SummaryResult(STATUS_OK, cached_label, "cache")
    resolved_key = (key or "").strip() or api_key()
    if not resolved_key:
        return SummaryResult(STATUS_NO_KEY, "", "no OPENROUTER_API_KEY")

    payload = {
        "model": resolved_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": cleaned_prompt},
        ],
        "max_tokens": 40,
        "temperature": 0.1,
        "reasoning": {"enabled": False},
    }
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(OPENROUTER_ENDPOINT, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {resolved_key}")
    req.add_header("Content-Type", "application/json")
    req.add_header("HTTP-Referer", HTTP_REFERER)
    req.add_header("X-Title", X_TITLE)

    try:
        with request.urlopen(req, timeout=request_timeout()) as resp:
            body = resp.read().decode("utf-8", "replace")
    except error.HTTPError as exc:
        try:
            err_body = exc.read().decode("utf-8", "replace")
        except Exception:
            err_body = ""
        status = _classify_http_error(exc.code, err_body)
        return SummaryResult(status, "", f"HTTP {exc.code}: {err_body[:200]}")
    except (error.URLError, TimeoutError, OSError) as exc:
        return SummaryResult(STATUS_ERROR, "", f"request failed: {exc}")

    try:
        parsed = json.loads(body)
        content = parsed["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        return SummaryResult(STATUS_ERROR, "", f"bad response: {exc}")

    label = clean_label(content or "")
    if not label:
        return SummaryResult(STATUS_ERROR, "", "empty label")
    write_cached_label(resolved_model, cleaned_prompt, label)
    return SummaryResult(STATUS_OK, label, "")


def human_project_name(path: str) -> str:
    """Convert a project path into a display name."""
    name = Path(path).name or path
    words = re.sub(r"[-_]+", " ", name).strip()
    return words[:1].upper() + words[1:] if words else "Project"


def normalize_task_description(task_description: str | None) -> str:
    """Normalize task text."""
    return " ".join((task_description or "").split()) if isinstance(task_description, str) else ""


def git_summary_for_project(project_path: str | None = "") -> str | None:
    """Return a compact Git branch label for terminal titles."""
    if not project_path or str(project_path).strip() in {"", "."}:
        return None
    cwd = str(Path(project_path).expanduser().resolve())
    try:
        branch_result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
            check=False,
        )
    except Exception:
        return None
    branch = (branch_result.stdout or "").strip() if branch_result.returncode == 0 else ""
    if not branch:
        return None
    return branch if branch != "HEAD" else "detached"


def project_git_context(project_path: str | None = "") -> str:
    """Return the visible project/Git context used in terminal titles."""
    label = human_project_name(project_path or "")
    git_summary = git_summary_for_project(project_path or "")
    if not git_summary:
        return label
    return f"{label} · {git_summary}"


def prompt_title_with_git_context(prompt: str, project_path: str | None = "") -> str:
    """Render a terminal title with active project/Git context."""
    raw_prompt = prompt.strip()
    normalized_prompt = normalize_task_description(raw_prompt) or ""
    display_prompt = normalized_prompt or raw_prompt[:40]
    if not raw_prompt:
        return project_git_context(project_path or "")
    context = project_git_context(project_path or "")
    max_title_length = 255
    if len(context) >= max_title_length - 4:
        return f"{context[:max_title_length - 3].rstrip()}..."
    available_prompt_chars = max(0, max_title_length - 4 - len(context))
    shortened_prompt = display_prompt[:available_prompt_chars].rstrip() + (
        "..." if len(display_prompt) > available_prompt_chars else ""
    )
    return f"{shortened_prompt} · {context}"


def _project_feature_label_path(project_path: str) -> Path:
    key = hashlib.sha256((project_path or "").strip().encode("utf-8")).hexdigest()
    return STATE_DIR / "feature-label" / f"{key}.txt"


def store_project_feature_label(project_path: str, label: str) -> None:
    """Persist the title label for the project."""
    label = (label or "").strip()
    if not label:
        return
    try:
        path = _project_feature_label_path(project_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(label, encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def read_project_feature_label(project_path: str) -> str:
    """Return the latest fresh label for a project."""
    try:
        path = _project_feature_label_path(project_path)
        if not path.is_file() or (time.time() - path.stat().st_mtime) > FEATURE_LABEL_TTL_SECONDS:
            return ""
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def describe_prompt(prompt: str, project_path: str = "") -> dict[str, str]:
    """Return title metadata for a prompt."""
    task = normalize_task_description(prompt)
    fallback = prompt_title_with_git_context(prompt, project_path)
    if not task:
        return {"status": STATUS_SKIPPED, "label": "", "title": fallback, "detail": "empty prompt"}
    result = summarize_prompt(task)
    if result.status == STATUS_OK and result.label:
        store_project_feature_label(project_path, result.label)
        return {
            "status": result.status,
            "label": result.label,
            "title": prompt_title_with_git_context(result.label, project_path),
            "detail": result.detail,
        }
    append_log(f"summary status={result.status} detail={result.detail}")
    return {"status": result.status, "label": "", "title": fallback, "detail": result.detail}


def completion_message(project_name: str, project_path: str = "") -> str:
    """Build a completion message from the latest stored feature label."""
    label = read_project_feature_label(project_path)
    if not label:
        return ""
    project_label = (
        project_name.strip()
        or (human_project_name(project_path) if project_path.strip() else "")
        or "this project"
    )
    return f"Project {project_label} done with {label}"


def set_terminal_prompt_title(
    title_text: str,
    max_length: int = 255,
    kitty_window_id: str | None = None,
    terminal_device: str | None = None,
) -> None:
    """Set and, in Kitty, lock the window title to the supplied text."""
    target_kitty_window = kitty_window_id or os.environ.get("KITTY_WINDOW_ID", "")
    if not title_text or (not target_kitty_window and not terminal_device and not sys.stdout.isatty()) or os.environ.get("TERM") == "dumb":
        return
    title = " ".join(title_text.split())
    title = "".join(char for char in title if char.isprintable())
    if not title:
        return
    if len(title) > max_length:
        title = f"{title[:max_length - 3].rstrip()}..."
    if target_kitty_window and shutil.which("kitty"):
        commands = [
            ["kitty", "@", "set-tab-title", "--match", f"window_id:{target_kitty_window}", title],
            ["kitty", "@", "set-window-title", "--match", f"id:{target_kitty_window}", title],
        ]
        results = [
            subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            for command in commands
        ]
        if all(result.returncode == 0 for result in results):
            return
    title_sequence = f"\033]2;{title}\007"
    if terminal_device:
        try:
            with Path(terminal_device).open("w", encoding="utf-8") as terminal:
                terminal.write(title_sequence)
                terminal.flush()
            return
        except OSError as exc:
            append_log(f"terminal title write failed device={terminal_device} error={exc}")
    if sys.stdout.isatty():
        sys.stdout.write(title_sequence)
        sys.stdout.flush()


def user_prompt_from_payload(payload: dict) -> str:
    """Return prompt text from a Claude hook payload."""
    for key in ("prompt", "message", "input"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())
    return ""


def cmd_describe(args: argparse.Namespace) -> int:
    """Print a prompt title description."""
    item = describe_prompt(args.prompt or "", args.project_path or "")
    if args.json:
        print(json.dumps(item, sort_keys=True))
    else:
        print(item["title"])
    return 0


def cmd_title(args: argparse.Namespace) -> int:
    """Summarize a prompt and set the terminal title."""
    item = describe_prompt(args.prompt or "", args.project_path or "")
    set_terminal_prompt_title(
        item["title"],
        kitty_window_id=args.kitty_window_id or None,
        terminal_device=args.terminal_device or None,
    )
    if args.print_title:
        print(item["title"])
    return 0


def cmd_completion_message(args: argparse.Namespace) -> int:
    """Print the latest stored completion message for a project."""
    message = completion_message(args.project_name or "", args.project_path or "")
    if message:
        print(message)
    return 0


def cmd_claude_hook(_args: argparse.Namespace) -> int:
    """Handle Claude Code UserPromptSubmit hook payloads from stdin."""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    if payload.get("hook_event_name") != "UserPromptSubmit":
        return 0
    prompt = user_prompt_from_payload(payload)
    project_path = os.environ.get("CODEX_AGENT_PROJECT_PATH", "") or payload.get("cwd", "")
    if not prompt:
        return 0
    item = describe_prompt(prompt, project_path)
    terminal_device = os.environ.get("CODEX_AGENT_TERMINAL_DEVICE", "")
    set_terminal_prompt_title(
        item["title"],
        kitty_window_id=os.environ.get("KITTY_WINDOW_ID", "") or None,
        terminal_device=terminal_device or None,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(description="Generate Kitty terminal titles for agent prompts.")
    sub = parser.add_subparsers(dest="command", required=True)

    describe = sub.add_parser("describe", help="print a summarized title")
    describe.add_argument("--prompt", required=True)
    describe.add_argument("--project-path", default="")
    describe.add_argument("--json", action="store_true")
    describe.set_defaults(func=cmd_describe)

    title = sub.add_parser("title", help="summarize a prompt and set the terminal title")
    title.add_argument("--prompt", required=True)
    title.add_argument("--project-path", default="")
    title.add_argument("--codex-home", default="")
    title.add_argument("--kitty-window-id", default="")
    title.add_argument("--kitty-listen-on", default="")
    title.add_argument("--kitty-pid", default="")
    title.add_argument("--terminal-device", default="")
    title.add_argument("--print-title", action="store_true")
    title.set_defaults(func=cmd_title)

    completion = sub.add_parser("completion-message", help="print the cached project completion message")
    completion.add_argument("--project-name", default="")
    completion.add_argument("--project-path", default="")
    completion.set_defaults(func=cmd_completion_message)

    claude_hook = sub.add_parser("claude-hook", help="handle Claude UserPromptSubmit hook payloads")
    claude_hook.set_defaults(func=cmd_claude_hook)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the command-line interface."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
