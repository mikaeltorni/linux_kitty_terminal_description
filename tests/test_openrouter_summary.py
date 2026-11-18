#!/usr/bin/env python3
"""Tests for the OpenRouter prompt summarizer owned by this repository.

The summarizer used to be duplicated inside Agent Command Center; it now lives
here as the single source of truth and Agent Command Center consumes it through
the ``kitty-terminal-description`` CLI. These tests never hit the network: every
HTTP interaction is stubbed so the billing-failure classification, label
cleanup, and safe-fallback behavior are exercised deterministically and offline.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import time
from pathlib import Path
from urllib import error

import pytest


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "kitty_terminal_description.py"


def _load_module():
    """Load the executable CLI as an importable module named ``summ``."""
    spec = importlib.util.spec_from_file_location("kitty_terminal_description", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


summ = _load_module()


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    """Point the summary cache at a throwaway dir so tests never touch the real one."""
    monkeypatch.setenv("OPENROUTER_SUMMARY_CACHE_DIR", str(tmp_path / "cache"))


def _fake_urlopen_returning(payload: dict):
    """Build a urlopen stub that yields a JSON response body."""

    class _Resp:
        """Minimal context-manager response wrapping a fixed JSON body."""

        def __enter__(self):
            """Enter the context manager and return the response itself."""
            return self

        def __exit__(self, *exc):
            """Exit the context manager without suppressing exceptions."""
            return False

        def read(self):
            """Return the encoded JSON payload bytes."""
            return json.dumps(payload).encode("utf-8")

    def _urlopen(req, timeout=None):
        """Return the canned response regardless of request or timeout."""
        return _Resp()

    return _urlopen


def _http_error(code: int, body: str) -> error.HTTPError:
    """Construct an ``HTTPError`` whose ``read()`` returns ``body``."""
    return error.HTTPError(
        url="https://openrouter.ai/api/v1/chat/completions",
        code=code,
        msg="error",
        hdrs=None,
        fp=io.BytesIO(body.encode("utf-8")),
    )


def test_clean_label_strips_punctuation_quotes_and_caps_words():
    """clean_label should yield a terse, speech-safe label."""
    assert summ.clean_label('"Persistent dark mode toggle."') == "Persistent dark mode toggle"
    assert summ.clean_label("Fix queue race condition\nextra line") == "Fix queue race condition"
    assert summ.clean_label("one two three four five six seven eight nine", max_words=4) == "one two three four"
    assert summ.clean_label("   ") == ""


def test_clean_label_strips_reasoning_think_tags():
    """Leaked <think> reasoning blocks must never reach the spoken label."""
    assert summ.clean_label("<think>let me consider</think>HTTP client retry") == "HTTP client retry"
    assert summ.clean_label("</think>Persistent dark mode toggle") == "Persistent dark mode toggle"
    assert summ.clean_label("<think>only reasoning, no answer") == ""


def test_summarize_prompt_skips_empty_prompt():
    """An empty prompt should be skipped without any network call."""
    assert summ.summarize_prompt("   ").status == summ.STATUS_SKIPPED


def test_summarize_prompt_reports_missing_key(monkeypatch):
    """A missing API key should report STATUS_NO_KEY, never raise."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(summ, "load_dotenv", lambda path=None: None)
    result = summ.summarize_prompt("add a dark mode toggle")
    assert result.status == summ.STATUS_NO_KEY
    assert result.label == ""


def test_summarize_prompt_returns_cleaned_label_on_success(monkeypatch):
    """A successful response should yield a cleaned feature label."""
    payload = {"choices": [{"message": {"content": '"Persistent dark mode toggle"'}}]}
    monkeypatch.setattr(summ.request, "urlopen", _fake_urlopen_returning(payload))
    result = summ.summarize_prompt("add a dark mode toggle", key="test-key")
    assert result.status == summ.STATUS_OK
    assert result.label == "Persistent dark mode toggle"


def test_summarize_prompt_reuses_cached_label_for_repeated_prompt(monkeypatch):
    """A repeated prompt must reuse the first label, not call the API again.

    This is the guarantee that keeps the Kitty title and the spoken finish
    announcement identical: both summarize the same prompt in separate
    processes, so the second summary has to come from the shared cache rather
    than a second, independently varying API call.
    """
    prompt = "fix the crash when dragging a window across monitors"
    first = {"choices": [{"message": {"content": "Fix window drag crash"}}]}
    monkeypatch.setattr(summ.request, "urlopen", _fake_urlopen_returning(first))
    initial = summ.summarize_prompt(prompt, key="k")
    assert initial.status == summ.STATUS_OK
    assert initial.label == "Fix window drag crash"

    calls = {"count": 0}
    different = {"choices": [{"message": {"content": "Fix window move crash"}}]}

    def _tripwire(req, timeout=None):
        """Count any second API call and return a divergent label if hit."""
        calls["count"] += 1
        return _fake_urlopen_returning(different)(req, timeout=timeout)

    monkeypatch.setattr(summ.request, "urlopen", _tripwire)
    repeated = summ.summarize_prompt(prompt, key="k")
    assert repeated.status == summ.STATUS_OK
    assert repeated.label == "Fix window drag crash"
    assert calls["count"] == 0


def test_summarize_prompt_cache_disabled_by_zero_ttl(monkeypatch):
    """A non-positive TTL disables the cache so every call hits the API."""
    monkeypatch.setenv("OPENROUTER_SUMMARY_CACHE_TTL", "0")
    prompt = "add a persistent dark mode toggle"
    calls = {"count": 0}

    def _counting(req, timeout=None):
        """Count each API call and return a fixed label."""
        calls["count"] += 1
        payload = {"choices": [{"message": {"content": "Dark mode toggle"}}]}
        return _fake_urlopen_returning(payload)(req, timeout=timeout)

    monkeypatch.setattr(summ.request, "urlopen", _counting)
    assert summ.summarize_prompt(prompt, key="k").label == "Dark mode toggle"
    assert summ.summarize_prompt(prompt, key="k").label == "Dark mode toggle"
    assert calls["count"] == 2


def test_summarize_prompt_classifies_rate_limit(monkeypatch):
    """HTTP 429 should be classified as a rate limit."""

    def _raise(req, timeout=None):
        """Raise a 429 HTTPError to simulate a rate limit."""
        raise _http_error(429, '{"error":"rate limit exceeded"}')

    monkeypatch.setattr(summ.request, "urlopen", _raise)
    assert summ.summarize_prompt("x", key="k").status == summ.STATUS_RATE_LIMITED


def test_summarize_prompt_classifies_out_of_credits(monkeypatch):
    """HTTP 402 should be classified as out of credits."""

    def _raise(req, timeout=None):
        """Raise a 402 HTTPError to simulate exhausted credits."""
        raise _http_error(402, '{"error":"insufficient credits"}')

    monkeypatch.setattr(summ.request, "urlopen", _raise)
    assert summ.summarize_prompt("x", key="k").status == summ.STATUS_OUT_OF_CREDITS


def test_summarize_prompt_classifies_credit_keyword_without_code(monkeypatch):
    """A credit-related message on a generic error code should map to credits."""

    def _raise(req, timeout=None):
        """Raise a 400 HTTPError carrying a credit-exhaustion message."""
        raise _http_error(400, '{"error":"You are out of credits"}')

    monkeypatch.setattr(summ.request, "urlopen", _raise)
    assert summ.summarize_prompt("x", key="k").status == summ.STATUS_OUT_OF_CREDITS


def test_summarize_prompt_network_error_is_generic_error(monkeypatch):
    """A transport error should map to STATUS_ERROR, never raise."""

    def _raise(req, timeout=None):
        """Raise a URLError to simulate a network failure."""
        raise error.URLError("no route to host")

    monkeypatch.setattr(summ.request, "urlopen", _raise)
    assert summ.summarize_prompt("x", key="k").status == summ.STATUS_ERROR


def test_summary_model_default_and_override(monkeypatch):
    """The model id should default to the constant and honor the env override."""
    monkeypatch.delenv("OPENROUTER_SUMMARY_MODEL", raising=False)
    assert summ.DEFAULT_SUMMARY_MODEL == "google/gemini-2.5-flash-lite"
    assert summ.summary_model() == summ.DEFAULT_SUMMARY_MODEL
    monkeypatch.setenv("OPENROUTER_SUMMARY_MODEL", "vendor/new-model")
    assert summ.summary_model() == "vendor/new-model"


def test_clean_title_prompt_strips_raw_and_goal_markers():
    """Title/summary surfaces must drop harness /goal and legacy RAW: markers."""
    assert summ.clean_title_prompt("  /goal fix console title  ") == "fix console title"
    assert summ.clean_title_prompt("RAW:/goal fix console title") == "fix console title"
    assert summ.clean_title_prompt("RAW:plain prompt") == "plain prompt"
    assert summ.clean_title_prompt("/goal") == ""
    assert summ.clean_title_prompt("/goalie rename") == "/goalie rename"
    assert summ.clean_title_prompt(None) == ""
    assert summ.normalize_task_description("/goal do the thing") == "do the thing"


def test_generate_text_forwards_deterministic_maximum_request_settings(monkeypatch):
    """Generic consumers can use Qwen Flash's full output setting at zero temperature."""
    captured: dict[str, object] = {}

    def _capture(req, timeout=None):
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        payload = {"choices": [{"message": {"content": "# Updated prompt"}}]}
        return _fake_urlopen_returning(payload)(req, timeout=timeout)

    monkeypatch.setattr(summ.request, "urlopen", _capture)

    result = summ.generate_text(
        "Edit only material changes.",
        "Complete Git context.",
        model=summ.DEFAULT_SUMMARY_MODEL,
        key="test-key",
        max_tokens=65_536,
        temperature=0.0,
    )

    assert result == summ.SummaryResult(summ.STATUS_OK, "# Updated prompt", "")
    assert captured["payload"] == {
        "model": summ.DEFAULT_SUMMARY_MODEL,
        "messages": [
            {"role": "system", "content": "Edit only material changes."},
            {"role": "user", "content": "Complete Git context."},
        ],
        "max_tokens": 65_536,
        "temperature": 0.0,
        "reasoning": {"enabled": False},
    }
    assert captured["timeout"] == summ.generate_timeout()


def test_generate_json_cli_reads_machine_request_and_prints_machine_result(
    monkeypatch,
    capsys,
):
    """The installed CLI keeps large prompts on stdin and JSON on stdout."""
    calls: list[dict[str, object]] = []

    def _generate(system_prompt, user_prompt, **kwargs):
        calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                **kwargs,
            }
        )
        return summ.SummaryResult(summ.STATUS_OK, "# Final", "")

    monkeypatch.setattr(summ, "generate_text", _generate)
    monkeypatch.setattr(
        summ.sys,
        "stdin",
        io.StringIO(
            json.dumps(
                {
                    "system_prompt": "Initial prompt contract",
                    "user_prompt": "All repository context",
                    "model": summ.DEFAULT_SUMMARY_MODEL,
                    "max_tokens": 65_536,
                    "temperature": 0.0,
                }
            )
        ),
    )

    assert summ.main(["generate-json"]) == 0

    assert calls == [
        {
            "system_prompt": "Initial prompt contract",
            "user_prompt": "All repository context",
            "model": summ.DEFAULT_SUMMARY_MODEL,
            "max_tokens": 65_536,
            "temperature": 0.0,
        }
    ]
    assert json.loads(capsys.readouterr().out) == {
        "status": "ok",
        "text": "# Final",
        "detail": "",
    }


def test_cache_round_trips_label():
    """write_cached_label and read_cached_label should round-trip a fresh label."""
    summ.write_cached_label("model-a", "prompt text", "Prompt label")
    assert summ.read_cached_label("model-a", "prompt text") == "Prompt label"
    assert summ.read_cached_label("model-a", "other prompt") is None


def test_cache_ttl_zero_disables_read_and_write(monkeypatch):
    """A non-positive TTL should disable both cache reads and writes."""
    monkeypatch.setenv("OPENROUTER_SUMMARY_CACHE_TTL", "0")
    summ.write_cached_label("model-a", "prompt text", "Prompt label")
    assert summ.read_cached_label("model-a", "prompt text") is None
    assert not list(summ.summary_cache_dir().glob("*.txt"))


def test_cache_expired_entry_is_ignored(monkeypatch):
    """Entries older than the configured TTL should behave as cache misses."""
    monkeypatch.setenv("OPENROUTER_SUMMARY_CACHE_TTL", "1")
    summ.write_cached_label("model-a", "prompt text", "Prompt label")
    cached_file = next(summ.summary_cache_dir().glob("*.txt"))
    old_timestamp = time.time() - 10
    os.utime(cached_file, (old_timestamp, old_timestamp))
    assert summ.read_cached_label("model-a", "prompt text") is None


def test_summary_cache_dir_honors_override(tmp_path, monkeypatch):
    """An explicit OPENROUTER_SUMMARY_CACHE_DIR should win over the default."""
    target = tmp_path / "explicit-cache"
    monkeypatch.setenv("OPENROUTER_SUMMARY_CACHE_DIR", str(target))
    assert summ.summary_cache_dir() == target


def test_describe_json_routes_around_the_logged_primary_model_throttle(monkeypatch, capsys):
    """Replay the logged upstream throttle through the Python CLI.
    Parameters: monkeypatch - isolated transport and credentials; capsys - CLI output capture.
    Returns: None.
    """
    print(f"monkeypatch={monkeypatch} capsys={capsys}", file=summ.sys.stderr)
    prompt = (ROOT / "tests" / "title_rate_limit_prompt.txt").read_text().strip()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("OPENROUTER_SUMMARY_MODEL", raising=False)
    monkeypatch.delenv("CODEX_AGENT_PROJECT_PATH", raising=False)
    monkeypatch.setattr(summ, "store_project_feature_label", lambda *_args: None)

    def respond(req, timeout=None):
        """Simulate OpenRouter rate-limiting the primary and trying the next model.
        Parameters: req - HTTP request; timeout - optional wait limit.
        Returns: the mock response when fallback routing is enabled.
        """
        print(f"req={req} timeout={timeout}", file=summ.sys.stderr)
        payload = json.loads(req.data)
        assert payload["messages"][-1]["content"] == prompt
        if payload.get("models") != ["google/gemini-2.5-flash-lite", "openai/gpt-4.1-nano"]:
            raise _http_error(429, "google/gemini-2.5-flash-lite is temporarily rate-limited upstream.")
        result = _fake_urlopen_returning(
            {"model": "openai/gpt-4.1-nano", "choices": [{"message": {"content": "Logo background placement"}}]}
        )(req, timeout=timeout)
        print(result, file=summ.sys.stderr)
        return result

    monkeypatch.setattr(summ.request, "urlopen", respond)
    assert summ.main(["describe", "--json", "--prompt", prompt, "--project-path", "/work/demo"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "ok", result
    assert result["label"] == "Logo background placement"
    assert result["title"] == "Logo background placement · Demo"
    print(None, file=summ.sys.stderr)
