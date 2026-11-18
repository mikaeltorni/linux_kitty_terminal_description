#!/usr/bin/env python3
"""kitty_terminal_description.py — Prompt summaries for Kitty terminal titles.

Components:
  - Centralized logging to .log/events.log via logging_utils.
  - OpenRouter summary helpers for short prompt labels.
  - Project/Git context helpers for readable terminal titles.
  - Feature-label cache shared by title and completion-message callers.
  - CLI subcommands for titles, commit messages, hooks, and generic text generation.

Usage:
  kitty-terminal-description describe --prompt "fix tests" --project-path "$PWD"
  kitty-terminal-description title --prompt "fix tests" --project-path "$PWD"
  printf '%s' '{"system_prompt":"...","user_prompt":"..."}' | kitty-terminal-description generate-json
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

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from logging_utils import get_logger


# Cheap OpenRouter model for titles/commits. qwen/qwen3.7-flash was the prior
# default but hits free-tier upstream 429s; override with OPENROUTER_SUMMARY_MODEL.
DEFAULT_SUMMARY_MODEL = "google/gemini-2.5-flash-lite"
TITLE_FALLBACK_MODEL = "openai/gpt-4.1-nano"
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
COMMIT_SYSTEM_PROMPT = (
    "You write one Conventional Commits subject for the supplied Git diff. "
    "Output exactly one line using type(scope): imperative summary. Allowed "
    "types: feat, fix, docs, style, refactor, perf, test, build, ci, chore, "
    "revert. Use a short lowercase scope when useful. Do not add markdown, a "
    "body, quotes, reasoning, or a trailing period. Keep it at most 100 characters."
)
_CONVENTIONAL_SUBJECT_RE = re.compile(
    r"^(?:feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)"
    r"(?:\([a-zA-Z0-9._/-]+\))?!?: .{1,100}$"
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

class SummaryResult(NamedTuple):
    """Outcome of a prompt-summary attempt.

    Attributes:
        status (str): One of the ``STATUS_*`` constants describing the outcome.
        label (str): The resulting feature label (empty unless ``status`` is
            :data:`STATUS_OK`).
        detail (str): Human-readable context, such as an error message or the
            ``"cache"`` source marker.
    """

    status: str
    label: str = ""
    detail: str = ""


logger = get_logger("kitty_terminal_description", "events.log")


def summary_model() -> str:
    """Resolve the OpenRouter summary model id.

    Returns:
        str: ``OPENROUTER_SUMMARY_MODEL`` when set and non-empty, otherwise
        :data:`DEFAULT_SUMMARY_MODEL`.
    """
    model = (os.environ.get("OPENROUTER_SUMMARY_MODEL") or "").strip() or DEFAULT_SUMMARY_MODEL
    logger.debug("summary_model resolved=%s", model)
    return model


def request_timeout() -> float:
    """Resolve the OpenRouter request timeout in seconds.

    Reads ``OPENROUTER_SUMMARY_TIMEOUT`` and falls back to ``8.0`` for unset,
    non-numeric, or non-positive values.

    Returns:
        float: A positive timeout in seconds.
    """
    raw = (os.environ.get("OPENROUTER_SUMMARY_TIMEOUT") or "").strip()
    try:
        value = float(raw)
    except ValueError:
        logger.debug("request_timeout invalid=%r using default=8.0", raw)
        return 8.0
    timeout = value if value > 0 else 8.0
    logger.debug("request_timeout resolved=%s", timeout)
    return timeout


def generate_timeout() -> float:
    """Resolve the timeout for long generic text-generation requests.

    Returns:
        float: ``OPENROUTER_GENERATE_TIMEOUT`` when positive, otherwise 600.
    """
    raw = (os.environ.get("OPENROUTER_GENERATE_TIMEOUT") or "").strip()
    try:
        value = float(raw)
    except ValueError:
        logger.debug("generate_timeout invalid=%r using default=600.0", raw)
        return 600.0
    timeout = value if value > 0 else 600.0
    logger.debug("generate_timeout resolved=%s", timeout)
    return timeout


def env_file_candidates() -> list[Path]:
    """Build the ordered list of env-file paths to probe, most specific first.

    Order: ``OPENROUTER_ENV_FILE`` override, the XDG config file, the
    repository-root ``.env``, and the ``.env`` beside the installed script.

    Returns:
        list[Path]: Candidate env-file paths in priority order.
    """
    candidates: list[Path] = []
    override = (os.environ.get("OPENROUTER_ENV_FILE") or "").strip()
    if override:
        candidates.append(Path(override).expanduser())
    config_home = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    candidates.append(Path(config_home) / "linux-kitty-terminal-description" / "openrouter.env")
    here = Path(__file__).resolve()
    candidates.append(here.parent.parent / ".env")
    candidates.append(here.parent / ".env")
    logger.debug("env_file_candidates count=%d", len(candidates))
    return candidates


def load_dotenv(path: Path | None = None) -> None:
    """Load ``KEY=VALUE`` pairs from the first readable env file into the env.

    Existing ``os.environ`` keys are never overwritten, so real environment
    variables always win over file contents. ``export`` prefixes and wrapping
    quotes are stripped. Processing stops after the first readable file.

    Args:
        path (Path | None): Explicit env file to load. When ``None`` the
            ordered :func:`env_file_candidates` list is probed instead.

    Returns:
        None
    """
    paths = [path] if path is not None else env_file_candidates()
    for env_path in paths:
        try:
            text = env_path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            continue
        logger.debug("load_dotenv loading file=%s", env_path)
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
    """Resolve the OpenRouter API key, loading env files on demand.

    Prefers an already-exported ``OPENROUTER_API_KEY``; otherwise loads the
    first available env file (see :func:`load_dotenv`) and re-reads it.

    Returns:
        str: The trimmed API key, or an empty string when none is configured.
    """
    key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if key:
        return key
    load_dotenv()
    key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    logger.debug("api_key resolved present=%s", bool(key))
    return key


def summary_cache_ttl() -> float:
    """Resolve the prompt-summary cache time-to-live in seconds.

    Reads ``OPENROUTER_SUMMARY_CACHE_TTL`` and falls back to
    :data:`DEFAULT_SUMMARY_CACHE_TTL_SECONDS` for unset or non-numeric values.
    A value ``<= 0`` disables caching at the call sites.

    Returns:
        float: The cache TTL in seconds.
    """
    raw = (os.environ.get("OPENROUTER_SUMMARY_CACHE_TTL") or "").strip()
    if not raw:
        return float(DEFAULT_SUMMARY_CACHE_TTL_SECONDS)
    try:
        return float(raw)
    except ValueError:
        logger.debug("summary_cache_ttl invalid=%r using default", raw)
        return float(DEFAULT_SUMMARY_CACHE_TTL_SECONDS)


def summary_cache_dir() -> Path:
    """Resolve the directory holding cached OpenRouter labels.

    Returns:
        Path: ``OPENROUTER_SUMMARY_CACHE_DIR`` when set, else the
        ``summary-cache`` subdirectory of :data:`STATE_DIR`.
    """
    override = (os.environ.get("OPENROUTER_SUMMARY_CACHE_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    return STATE_DIR / "summary-cache"


def _summary_cache_path(model: str, prompt: str) -> Path:
    """Compute the cache file path for a model/prompt pair.

    Args:
        model (str): Resolved OpenRouter model id.
        prompt (str): Whitespace-normalized prompt text.

    Returns:
        Path: A ``<sha256>.txt`` path inside :func:`summary_cache_dir`.
    """
    digest = hashlib.sha256(f"{model}\n{prompt}".encode("utf-8")).hexdigest()
    return summary_cache_dir() / f"{digest}.txt"


def read_cached_label(model: str, prompt: str) -> str | None:
    """Return a still-fresh cached label for a model/prompt pair.

    Args:
        model (str): Resolved OpenRouter model id.
        prompt (str): Whitespace-normalized prompt text.

    Returns:
        str | None: The cached label when present and within the TTL window,
        otherwise ``None`` (also on disabled caching or read errors).
    """
    ttl = summary_cache_ttl()
    if ttl <= 0:
        return None
    try:
        path = _summary_cache_path(model, prompt)
        if not path.is_file() or (time.time() - path.stat().st_mtime) > ttl:
            logger.debug("read_cached_label miss model=%s", model)
            return None
        label = path.read_text(encoding="utf-8").strip() or None
        logger.debug("read_cached_label hit=%s model=%s", bool(label), model)
        return label
    except OSError as exc:
        logger.warning("read_cached_label failed model=%s error=%s", model, exc)
        return None


def write_cached_label(model: str, prompt: str, label: str) -> None:
    """Persist a prompt label so paired title/completion calls reuse it.

    Writes atomically via a temporary file. No-ops when the label is empty or
    caching is disabled, and swallows filesystem errors.

    Args:
        model (str): Resolved OpenRouter model id.
        prompt (str): Whitespace-normalized prompt text.
        label (str): Feature label to cache.

    Returns:
        None
    """
    if not label or summary_cache_ttl() <= 0:
        return
    try:
        path = _summary_cache_path(model, prompt)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(label, encoding="utf-8")
        tmp.replace(path)
        logger.debug("write_cached_label stored model=%s", model)
    except OSError as exc:
        logger.warning("write_cached_label failed model=%s error=%s", model, exc)


def clean_label(text: str, *, max_words: int = 8, max_chars: int = 64) -> str:
    """Normalize a raw model response into a safe, terse feature label.

    Strips ``<think>`` reasoning blocks, takes the first line, removes quotes
    and markdown punctuation, drops trailing separators, then caps the result
    by word and character count.

    Args:
        text (str): Raw model output to clean.
        max_words (int): Maximum number of words to keep (``<= 0`` disables the
            word cap). Defaults to ``8``.
        max_chars (int): Maximum character length before truncation on a word
            boundary. Defaults to ``64``.

    Returns:
        str: A trimmed feature label, or an empty string for empty input.
    """
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
    """Map an OpenRouter HTTP failure to a coarse status string.

    Args:
        code (int): HTTP status code from the failed response.
        body (str): Response body text used for keyword heuristics.

    Returns:
        str: :data:`STATUS_RATE_LIMITED`, :data:`STATUS_OUT_OF_CREDITS`, or
        :data:`STATUS_ERROR`.
    """
    lowered = (body or "").lower()
    if code == 429 or "rate limit" in lowered or "rate-limit" in lowered:
        return STATUS_RATE_LIMITED
    if code == 402 or "insufficient" in lowered or "out of credit" in lowered or "credits" in lowered or "quota" in lowered:
        return STATUS_OUT_OF_CREDITS
    return STATUS_ERROR


def _request_openrouter_content(
    messages: list[dict[str, str]],
    *,
    model: str,
    key: str | None,
    max_tokens: int,
    event: str,
    temperature: float = 0.1,
    timeout: float | None = None,
    fallback_model: str | None = None,
) -> SummaryResult:
    """Request one non-reasoning OpenRouter completion through shared plumbing.

    Parameters: messages - ordered chat messages; model - primary model ID;
        key - optional credential override; max_tokens - output limit; event - operation label;
        temperature - sampling temperature; timeout - wait limit; fallback_model - optional backup model ID.
    Returns: model content or a classified failure.
    """
    print(f"messages={messages} model={model} key={'<redacted>' if key else key} max_tokens={max_tokens} "
          f"event={event} temperature={temperature} timeout={timeout} fallback_model={fallback_model}", file=sys.stderr)
    resolved_key = (key or "").strip() or api_key()
    if not resolved_key:
        logger.warning("%s missing OPENROUTER_API_KEY", event)
        result = SummaryResult(STATUS_NO_KEY, "", "no OPENROUTER_API_KEY")
        print(result, file=sys.stderr)
        return result
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": float(temperature),
        "reasoning": {"enabled": False},
    }
    if fallback_model:
        del payload["model"]
        payload["models"] = [model, fallback_model]
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(OPENROUTER_ENDPOINT, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {resolved_key}")
    req.add_header("Content-Type", "application/json")
    req.add_header("HTTP-Referer", HTTP_REFERER)
    req.add_header("X-Title", X_TITLE)
    logger.info("%s requesting model=%s", event, model)
    try:
        with request.urlopen(req, timeout=request_timeout() if timeout is None else timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
    except error.HTTPError as exc:
        try:
            err_body = exc.read().decode("utf-8", "replace")
        except (OSError, ValueError):
            err_body = ""
        status = _classify_http_error(exc.code, err_body)
        logger.warning("%s http error code=%s status=%s", event, exc.code, status)
        result = SummaryResult(status, "", f"HTTP {exc.code}: {err_body[:200]}")
        print(result, file=sys.stderr)
        return result
    except (error.URLError, TimeoutError, OSError) as exc:
        logger.warning("%s request failed error=%s", event, exc)
        result = SummaryResult(STATUS_ERROR, "", f"request failed: {exc}")
        print(result, file=sys.stderr)
        return result
    try:
        parsed = json.loads(body)
        content = parsed["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        logger.warning("%s bad response error=%s", event, exc)
        result = SummaryResult(STATUS_ERROR, "", f"bad response: {exc}")
        print(result, file=sys.stderr)
        return result
    if not isinstance(content, str) or not content.strip():
        logger.warning("%s produced empty content model=%s", event, model)
        result = SummaryResult(STATUS_ERROR, "", "empty response")
        print(result, file=sys.stderr)
        return result
    result = SummaryResult(STATUS_OK, content, "")
    print(result, file=sys.stderr)
    return result


def generate_text(
    system_prompt: str,
    user_prompt: str,
    *,
    model: str | None = None,
    key: str | None = None,
    max_tokens: int = 65_536,
    temperature: float = 0.0,
) -> SummaryResult:
    """Generate arbitrary text through the shared OpenRouter transport.

    Args:
        system_prompt: Instruction message defining the response contract.
        user_prompt: Complete user context; this helper does not truncate it.
        model: Optional model id, defaulting to the shared Qwen Flash model.
        key: Optional API key override.
        max_tokens: Positive response-token ceiling.
        temperature: Sampling temperature sent to OpenRouter.

    Returns:
        SummaryResult: Generated text is returned in ``label`` on success.
    """
    cleaned_system = (system_prompt or "").strip()
    cleaned_user = (user_prompt or "").strip()
    if not cleaned_system or not cleaned_user:
        logger.warning("generate_text skipped empty prompt")
        return SummaryResult(STATUS_SKIPPED, "", "empty prompt")
    if int(max_tokens) <= 0:
        return SummaryResult(STATUS_ERROR, "", "max_tokens must be positive")
    return _request_openrouter_content(
        [
            {"role": "system", "content": cleaned_system},
            {"role": "user", "content": cleaned_user},
        ],
        model=model or DEFAULT_SUMMARY_MODEL,
        key=key,
        max_tokens=int(max_tokens),
        temperature=float(temperature),
        timeout=generate_timeout(),
        event="generate_text",
    )


def summarize_prompt(prompt: str, *, model: str | None = None, key: str | None = None) -> SummaryResult:
    """Compress a prompt into a short OpenRouter feature label.

    Returns a cached label when available, otherwise calls the OpenRouter chat
    completions endpoint and caches a successful result. All network and
    parsing failures are reported as structured statuses rather than raised.

    Parameters: prompt - task description; model - optional model override; key - optional credential override.
    Returns: cleaned AI label or a classified failure.
    """
    print(f"prompt={prompt} model={model} key={'<redacted>' if key else key}", file=sys.stderr)
    cleaned_prompt = " ".join((prompt or "").split())
    if not cleaned_prompt:
        logger.debug("summarize_prompt skipped empty prompt")
        result = SummaryResult(STATUS_SKIPPED, "", "empty prompt")
        print(result, file=sys.stderr)
        return result
    resolved_model = model or summary_model()
    cached_label = read_cached_label(resolved_model, cleaned_prompt)
    if cached_label:
        logger.info("summarize_prompt cache hit model=%s", resolved_model)
        result = SummaryResult(STATUS_OK, cached_label, "cache")
        print(result, file=sys.stderr)
        return result
    generated = _request_openrouter_content(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": cleaned_prompt},
        ],
        model=resolved_model,
        key=key,
        max_tokens=40,
        event="summarize_prompt",
        fallback_model=TITLE_FALLBACK_MODEL if model is None and resolved_model == DEFAULT_SUMMARY_MODEL else None,
    )
    if generated.status != STATUS_OK:
        print(generated, file=sys.stderr)
        return generated
    label = clean_label(generated.label)
    if not label:
        logger.warning("summarize_prompt produced empty label model=%s", resolved_model)
        result = SummaryResult(STATUS_ERROR, "", "empty label")
        print(result, file=sys.stderr)
        return result
    write_cached_label(resolved_model, cleaned_prompt, label)
    logger.info("summarize_prompt ok model=%s", resolved_model)
    result = SummaryResult(STATUS_OK, label, "")
    print(result, file=sys.stderr)
    return result


def clean_commit_subject(text: str) -> str:
    """Return one valid Conventional Commits subject or an empty string.

    Args:
        text: Raw model response, potentially containing reasoning or extra lines.
    """
    if not text:
        return ""
    cleaned = re.sub(r"<think>.*?</think>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"<think>.*$", " ", cleaned, flags=re.DOTALL | re.IGNORECASE)
    first = cleaned.strip().splitlines()[0].strip().strip('"').strip("'").strip("`")
    first = first.removesuffix(".")
    if not _CONVENTIONAL_SUBJECT_RE.fullmatch(first):
        return ""
    return first


def generate_commit_message(
    diff: str,
    *,
    project_path: str = "",
    model: str | None = None,
    key: str | None = None,
) -> SummaryResult:
    """Generate one Qwen-backed conventional subject for a Git diff.

    The first 120,000 characters are sent because very large or generated
    worktrees can otherwise exceed provider request limits. The user-selected
    diff is the only repository content sent to OpenRouter.

    Args:
        diff: Unified Git diff, including synthetic untracked-file additions.
        project_path: Display-only repository identity included in the prompt.
        model: Optional model override; defaults to the same Qwen used for titles.
        key: Optional API-key override.
    """
    cleaned_diff = (diff or "").strip()
    if not cleaned_diff:
        return SummaryResult(STATUS_SKIPPED, "", "empty diff")
    resolved_model = (
        model
        or (os.environ.get("OPENROUTER_COMMIT_MODEL") or "").strip()
        or DEFAULT_SUMMARY_MODEL
    )
    project = project_path or "(unknown repository)"
    generated = _request_openrouter_content(
        [
            {"role": "system", "content": COMMIT_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Repository: {project}\n\nGit diff:\n{cleaned_diff[:120_000]}",
            },
        ],
        model=resolved_model,
        key=key,
        max_tokens=80,
        event="generate_commit_message",
    )
    if generated.status != STATUS_OK:
        return generated
    subject = clean_commit_subject(generated.label)
    if not subject:
        logger.warning("generate_commit_message invalid subject model=%s", resolved_model)
        return SummaryResult(STATUS_ERROR, "", "invalid conventional commit subject")
    logger.info("generate_commit_message ok model=%s", resolved_model)
    return SummaryResult(STATUS_OK, subject, "")


def human_project_name(path: str) -> str:
    """Convert a filesystem path into a human-friendly project display name.

    Uses the final path component, replaces ``-``/``_`` runs with spaces, and
    capitalizes the first letter.

    Args:
        path (str): Project path (or bare name) to humanize.

    Returns:
        str: A display name, or ``"Project"`` when the input is empty.
    """
    name = Path(path).name or path
    words = re.sub(r"[-_]+", " ", name).strip()
    return words[:1].upper() + words[1:] if words else "Project"


def clean_title_prompt(prompt: str | None) -> str:
    """Normalize prompt text for user-visible titles and summary fallbacks.

    Collapses whitespace, strips a leading ``RAW:`` marker (legacy session
    fallbacks), and strips one leading harness ``/goal`` token. The agent
    launch prompt itself must keep ``/goal`` — only call this for titles,
    OpenRouter summary input, and similar display/summary surfaces.

    Args:
        prompt (str | None): Raw task text, possibly ``None``.

    Returns:
        str: Cleaned prompt text, or an empty string for non-strings / empty input.
    """
    if not isinstance(prompt, str):
        return ""
    text = " ".join(prompt.split())
    if text.startswith("RAW:"):
        text = text[4:].lstrip()
        logger.debug("clean_title_prompt stripped RAW: marker")
    if text == "/goal" or text.startswith("/goal "):
        text = text[5:].lstrip()
        logger.debug("clean_title_prompt stripped leading /goal")
    return text


def normalize_task_description(task_description: str | None) -> str:
    """Collapse whitespace and strip harness markers from a task description.

    Args:
        task_description (str | None): Raw task text, possibly ``None``.

    Returns:
        str: Whitespace-normalized title-safe text, or empty for non-strings.
    """
    return clean_title_prompt(task_description)


def git_summary_for_project(project_path: str | None = "") -> str | None:
    """Return a compact Git worktree label for use in terminal titles.

    Args:
        project_path (str | None): Path to a working tree. Empty, ``"."`` or
            ``None`` short-circuit to ``None``.

    Returns:
        str | None: The basename of the Git worktree root (the worktree
        name), or ``None`` when the path is not a usable Git repository.
    """
    if not project_path or str(project_path).strip() in {"", "."}:
        return None
    cwd = str(Path(project_path).expanduser().resolve())
    try:
        toplevel_result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        logger.debug("git_summary_for_project failed path=%s error=%s", project_path, exc)
        return None
    toplevel = (toplevel_result.stdout or "").strip() if toplevel_result.returncode == 0 else ""
    if not toplevel:
        return None
    return Path(toplevel).name or None


def project_git_context(project_path: str | None = "") -> str:
    """Render the project/Git context segment shown in terminal titles.

    Args:
        project_path (str | None): Path to the active project.

    Returns:
        str: ``"<Project>"`` or ``"<Project> · <worktree>"`` when a Git
        worktree name is available and differs from the project name (the
        segment is omitted in a primary checkout, where they are identical).
    """
    label = human_project_name(project_path or "")
    git_summary = git_summary_for_project(project_path or "")
    if not git_summary or human_project_name(git_summary) == label:
        return label
    return f"{label} · {git_summary}"


def prompt_title_with_git_context(prompt: str, project_path: str | None = "") -> str:
    """Compose a terminal title from a prompt plus project/Git context.

    The prompt is normalized and truncated so the combined title fits within a
    255-character budget, preferring to trim the prompt over the context.

    Args:
        prompt (str): Prompt or feature label to lead the title.
        project_path (str | None): Path used to derive the trailing context.

    Returns:
        str: A title string no longer than 255 characters.
    """
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
    """Compute the per-project feature-label cache file path.

    Args:
        project_path (str): Project path used as the cache key.

    Returns:
        Path: A ``<sha256>.txt`` path inside ``STATE_DIR/feature-label``.
    """
    key = hashlib.sha256((project_path or "").strip().encode("utf-8")).hexdigest()
    return STATE_DIR / "feature-label" / f"{key}.txt"


def store_project_feature_label(project_path: str, label: str) -> None:
    """Persist the most recent feature label for a project.

    Written atomically and consumed later by :func:`completion_message`.
    No-ops on an empty label and swallows filesystem errors.

    Args:
        project_path (str): Project path used as the cache key.
        label (str): Feature label to store.

    Returns:
        None
    """
    label = (label or "").strip()
    if not label:
        return
    try:
        path = _project_feature_label_path(project_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(label, encoding="utf-8")
        tmp.replace(path)
        logger.debug("store_project_feature_label stored path=%s", project_path)
    except OSError as exc:
        logger.warning("store_project_feature_label failed path=%s error=%s", project_path, exc)


def read_project_feature_label(project_path: str) -> str:
    """Return the latest still-fresh feature label for a project.

    Args:
        project_path (str): Project path used as the cache key.

    Returns:
        str: The cached label when present and within
        :data:`FEATURE_LABEL_TTL_SECONDS`, otherwise an empty string.
    """
    try:
        path = _project_feature_label_path(project_path)
        if not path.is_file() or (time.time() - path.stat().st_mtime) > FEATURE_LABEL_TTL_SECONDS:
            return ""
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.warning("read_project_feature_label failed path=%s error=%s", project_path, exc)
        return ""


def describe_prompt(prompt: str, project_path: str = "") -> dict[str, str]:
    """Build title metadata for a prompt, summarizing when possible.

    Attempts an OpenRouter summary; on success the label is cached for
    completion messages and woven into a contextual title. On any non-OK
    status it falls back to a raw prompt title and logs the reason.

    Args:
        prompt (str): Free-form prompt text.
        project_path (str): Project path used for context and label caching.

    Returns:
        dict[str, str]: Keys ``status``, ``label``, ``title``, and ``detail``.
    """
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
    logger.info("describe_prompt fallback status=%s detail=%s", result.status, result.detail)
    return {"status": result.status, "label": "", "title": fallback, "detail": result.detail}


def completion_message(project_name: str, project_path: str = "") -> str:
    """Build a completion message from the latest stored feature label.

    Args:
        project_name (str): Explicit display name; when blank it is derived
            from ``project_path`` (or falls back to ``"this project"``).
        project_path (str): Project path used to look up the cached label and
            derive a name.

    Returns:
        str: ``"Project <name> done with <label>"``, or an empty string when no
        fresh feature label is cached.
    """
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
    """Set, and within Kitty lock, the terminal window title.

    Prefers Kitty remote-control commands when a window id and the ``kitty``
    binary are available; otherwise emits an OSC 2 escape sequence to the given
    terminal device or stdout. No-ops on empty/unprintable titles, dumb
    terminals, or when no usable output target exists.

    Args:
        title_text (str): Desired title; whitespace-collapsed and stripped of
            unprintable characters before use.
        max_length (int): Maximum title length before truncation. Defaults to
            ``255``.
        kitty_window_id (str | None): Target Kitty window id; falls back to the
            ``KITTY_WINDOW_ID`` environment variable.
        terminal_device (str | None): Path to a TTY device to receive the OSC
            escape when Kitty remote control is unavailable.

    Returns:
        None
    """
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
            logger.debug("set_terminal_prompt_title via kitty window=%s", target_kitty_window)
            return
    title_sequence = f"\033]2;{title}\007"
    if terminal_device:
        try:
            with Path(terminal_device).open("w", encoding="utf-8") as terminal:
                terminal.write(title_sequence)
                terminal.flush()
            logger.debug("set_terminal_prompt_title via device=%s", terminal_device)
            return
        except OSError as exc:
            logger.warning("terminal title write failed device=%s error=%s", terminal_device, exc)
    if sys.stdout.isatty():
        sys.stdout.write(title_sequence)
        sys.stdout.flush()


def user_prompt_from_payload(payload: dict) -> str:
    """Extract prompt text from a Claude hook payload.

    Args:
        payload (dict): Decoded hook JSON; the first non-empty string among the
            ``prompt``, ``message``, and ``input`` keys is used.

    Returns:
        str: Whitespace-normalized prompt text, or an empty string when absent.
    """
    for key in ("prompt", "message", "input"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())
    return ""


def cmd_describe(args: argparse.Namespace) -> int:
    """Handle the ``describe`` subcommand: print a summarized title.

    Args:
        args (argparse.Namespace): Parsed args with ``prompt``, ``project_path``
            and ``json`` attributes.

    Returns:
        int: Process exit code (always ``0``).
    """
    item = describe_prompt(args.prompt or "", args.project_path or "")
    if args.json:
        print(json.dumps(item, sort_keys=True))
    else:
        print(item["title"])
    return 0


def cmd_title(args: argparse.Namespace) -> int:
    """Handle the ``title`` subcommand: summarize and set the terminal title.

    Args:
        args (argparse.Namespace): Parsed args with ``prompt``,
            ``project_path``, ``kitty_window_id``, ``terminal_device`` and
            ``print_title`` attributes.

    Returns:
        int: Process exit code (always ``0``).
    """
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
    """Handle the ``completion-message`` subcommand.

    Args:
        args (argparse.Namespace): Parsed args with ``project_name`` and
            ``project_path`` attributes.

    Returns:
        int: Process exit code (always ``0``). Prints nothing when no cached
        label exists.
    """
    message = completion_message(args.project_name or "", args.project_path or "")
    if message:
        print(message)
    return 0


def cmd_commit_message(args: argparse.Namespace) -> int:
    """Read a Git diff from stdin and print one Qwen-generated commit subject.

    Args:
        args: Parsed namespace containing ``project_path``.

    Returns:
        ``0`` on success and ``2`` when credentials, network, or validation fail.
    """
    result = generate_commit_message(
        sys.stdin.read(),
        project_path=args.project_path or "",
    )
    if result.status == STATUS_OK:
        print(result.label)
        return 0
    detail = result.detail or result.status
    print(detail, file=sys.stderr)
    return 2


def cmd_generate_json(_args: argparse.Namespace) -> int:
    """Read a generic generation request and print a structured JSON result.

    The protocol always exits successfully so callers can classify provider
    failures from the returned ``status`` without parsing stderr.

    Args:
        _args: Unused parsed namespace; the request is read from stdin.

    Returns:
        int: Always ``0`` after emitting one JSON object.
    """
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            raise ValueError("request must be a JSON object")
        result = generate_text(
            str(payload.get("system_prompt") or ""),
            str(payload.get("user_prompt") or ""),
            model=str(payload.get("model") or "") or None,
            max_tokens=int(payload.get("max_tokens", 65_536)),
            temperature=float(payload.get("temperature", 0.0)),
        )
    except (TypeError, ValueError) as exc:
        logger.warning("cmd_generate_json invalid request error=%s", exc)
        result = SummaryResult(STATUS_ERROR, "", f"invalid request: {exc}")
    print(
        json.dumps(
            {"status": result.status, "text": result.label, "detail": result.detail},
            sort_keys=True,
        )
    )
    return 0


def cmd_claude_hook(_args: argparse.Namespace) -> int:
    """Handle the ``claude-hook`` subcommand for ``UserPromptSubmit`` payloads.

    Reads a hook JSON payload from stdin and, for ``UserPromptSubmit`` events,
    derives a title and applies it to the terminal. Malformed input, non-matching
    events, and empty prompts are silently ignored so the hook never disrupts
    Claude Code.

    Args:
        _args (argparse.Namespace): Unused parsed args (the payload is stdin).

    Returns:
        int: Process exit code (always ``0``).
    """
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        logger.debug("cmd_claude_hook ignoring invalid JSON payload")
        return 0
    if payload.get("hook_event_name") != "UserPromptSubmit":
        return 0
    prompt = user_prompt_from_payload(payload)
    project_path = os.environ.get("CODEX_AGENT_PROJECT_PATH", "") or payload.get("cwd", "")
    if not prompt:
        logger.debug("cmd_claude_hook empty prompt; skipping")
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
    """Construct the argparse CLI with all subcommands.

    Subcommands: ``describe``, ``title``, ``completion-message``,
    ``commit-message``, ``generate-json`` and ``claude-hook``. The ``title`` parser also accepts ``--codex-home``,
    ``--kitty-listen-on`` and ``--kitty-pid`` for backward compatibility with
    external callers (Agent Command Center); they are intentionally accepted
    and ignored so passing them never errors.

    Returns:
        argparse.ArgumentParser: A parser whose subcommands set ``func``.
    """
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
    # --codex-home, --kitty-listen-on and --kitty-pid are accepted but unused;
    # they preserve compatibility with external callers that still pass them.
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

    commit = sub.add_parser(
        "commit-message",
        help="read a Git diff from stdin and print a Qwen conventional subject",
    )
    commit.add_argument("--project-path", default="")
    commit.set_defaults(func=cmd_commit_message)

    generate = sub.add_parser(
        "generate-json",
        help="read a JSON generation request from stdin and print JSON",
    )
    generate.set_defaults(func=cmd_generate_json)

    claude_hook = sub.add_parser("claude-hook", help="handle Claude UserPromptSubmit hook payloads")
    claude_hook.set_defaults(func=cmd_claude_hook)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point: parse arguments and dispatch the selected command.

    Args:
        argv (list[str] | None): Argument vector; defaults to ``sys.argv[1:]``.

    Returns:
        int: The selected subcommand's exit code.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    logger.debug("main dispatching command=%s", getattr(args, "command", ""))
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
