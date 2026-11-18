//! Prompt summaries. A missing key keeps the raw title and does not call the network.

use crate::label::clean_label;
use crate::title::{clean_title_prompt, prompt_title};
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

const DEFAULT_SUMMARY_MODEL: &str = "google/gemini-2.5-flash-lite";
const TITLE_FALLBACK_MODEL: &str = "openai/gpt-4.1-nano";

pub struct Described {
    pub status: String,
    pub label: String,
    pub title: String,
    pub detail: String,
}

pub fn describe_prompt(prompt: &str, project_path: &str) -> Described {
    let task = clean_title_prompt(prompt);
    let fallback = prompt_title(prompt, project_path);
    if task.is_empty() {
        return Described { status: "skipped".into(), label: String::new(), title: fallback, detail: "empty prompt".into() };
    }
    if api_key().is_empty() {
        return Described { status: "no_key".into(), label: String::new(), title: fallback, detail: "no OPENROUTER_API_KEY".into() };
    }
    let generated = request_summary(&task);
    if generated.status == "ok" {
        let label = clean_label(&generated.label, 8, 64);
        if label.is_empty() {
            return Described { status: "error".into(), label: String::new(), title: fallback, detail: "empty label".into() };
        }
        return Described { status: "ok".into(), title: prompt_title(&label, project_path), label, detail: generated.detail };
    }
    Described { status: generated.status, label: String::new(), title: fallback, detail: generated.detail }
}

#[derive(Debug)]
pub struct Generated {
    pub status: String,
    pub label: String,
    pub detail: String,
}

/// Generate and validate a conventional commit subject.
/// Parameters: diff - Git diff; project_path - repository path.
/// Returns: generated subject or failure.
pub fn commit_subject(diff: &str, project_path: &str) -> Generated {
    eprintln!("diff={diff} project_path={project_path}");
    let cleaned = diff.trim();
    if cleaned.is_empty() {
        let result = Generated { status: "skipped".into(), label: String::new(), detail: "empty diff".into() };
        eprintln!("{result:?}");
        return result;
    }
    let model = env::var("OPENROUTER_COMMIT_MODEL").unwrap_or_default();
    let model = if model.trim().is_empty() { summary_model() } else { model.trim().to_string() };
    let project = if project_path.trim().is_empty() { "(unknown repository)" } else { project_path };
    let clipped: String = cleaned.chars().take(120_000).collect();
    let generated = request_chat(
        "You write one Conventional Commits subject for the supplied Git diff. Output exactly one line using type(scope): imperative summary. Allowed types: feat, fix, docs, style, refactor, perf, test, build, ci, chore, revert. Use a short lowercase scope when useful. Do not add markdown, a body, quotes, reasoning, or a trailing period. Keep it at most 100 characters.",
        &format!("Repository: {project}\n\nGit diff:\n{clipped}"),
        &[&model],
        80,
        0.1,
    );
    if generated.status != "ok" {
        let result = generated;
        eprintln!("{result:?}");
        return result;
    }
    let subject = clean_commit_subject(&generated.label);
    if subject.is_empty() {
        let result = Generated { status: "error".into(), label: String::new(), detail: "invalid conventional commit subject".into() };
        eprintln!("{result:?}");
        return result;
    }
    let result = Generated { status: "ok".into(), label: subject, detail: String::new() };
    eprintln!("{result:?}");
    result
}

/// Generate text with one model.
/// Parameters: system_prompt - instructions; user_prompt - input text; max_tokens - output limit.
/// Returns: model content or failure.
pub fn generate_text(system_prompt: &str, user_prompt: &str, max_tokens: i64) -> Generated {
    eprintln!("system_prompt={system_prompt} user_prompt={user_prompt} max_tokens={max_tokens}");
    let system = system_prompt.trim();
    let user = user_prompt.trim();
    if system.is_empty() || user.is_empty() {
        let result = Generated { status: "skipped".into(), label: String::new(), detail: "empty prompt".into() };
        eprintln!("{result:?}");
        return result;
    }
    if max_tokens <= 0 {
        let result = Generated { status: "error".into(), label: String::new(), detail: "max_tokens must be positive".into() };
        eprintln!("{result:?}");
        return result;
    }
    let result = request_chat(system, user, &[&summary_model()], max_tokens, 0.0);
    eprintln!("{result:?}");
    result
}

/// Resolve the configured summary model.
/// Parameters: none.
/// Returns: configured model or the default.
fn summary_model() -> String {
    eprintln!("parameters=none");
    let model = env::var("OPENROUTER_SUMMARY_MODEL").unwrap_or_default();
    let result = if model.trim().is_empty() { DEFAULT_SUMMARY_MODEL.into() } else { model.trim().to_string() };
    eprintln!("{result:?}");
    result
}

/// Request a title using the default model and its backup, or the configured model.
/// Parameters: prompt - normalized task text.
/// Returns: the generated label or provider failure.
fn request_summary(prompt: &str) -> Generated {
    eprintln!("prompt={prompt}");
    let model = summary_model();
    let models = if model == DEFAULT_SUMMARY_MODEL {
        vec![model.as_str(), TITLE_FALLBACK_MODEL]
    } else {
        vec![model.as_str()]
    };
    let result = request_chat(
        "You compress a software task description into a short feature label of two to five words. Output only the label in plain words. No punctuation, no quotes, no trailing period.",
        prompt,
        &models,
        40,
        0.1,
    );
    eprintln!("{result:?}");
    result
}

/// Submit a chat request using the supplied routing order.
/// Parameters: system_prompt - model instructions; user_prompt - input text; models - nonempty ordered model IDs; max_tokens - output limit; temperature - sampling temperature.
/// Returns: response content or a structured failure.
fn request_chat(system_prompt: &str, user_prompt: &str, models: &[&str], max_tokens: i64, temperature: f64) -> Generated {
    eprintln!("system_prompt={system_prompt} user_prompt={user_prompt} models={models:?} max_tokens={max_tokens} temperature={temperature}");
    let key = api_key();
    if key.is_empty() {
        let result = Generated { status: "no_key".into(), label: String::new(), detail: "no OPENROUTER_API_KEY".into() };
        eprintln!("{result:?}");
        return result;
    }
    let mut body = serde_json::json!({
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "reasoning": {"enabled": false}
    });
    if let [model] = models {
        body["model"] = serde_json::json!(model);
    } else {
        body["models"] = serde_json::json!(models);
    }
    let body = body.to_string();
    let mut child = Command::new("curl");
    child.args(["-sS", "-m", "8", "-X", "POST", "https://openrouter.ai/api/v1/chat/completions", "-H", &format!("Authorization: Bearer {key}"), "-H", "Content-Type: application/json", "-H", "HTTP-Referer: https://github.com/mikaeltorni/linux_kitty_terminal_description", "-H", "X-Title: Linux Kitty Terminal Description", "--data-binary", &body, "-w", "\n%{http_code}"]);
    let Ok(output) = child.output() else {
        let result = Generated { status: "error".into(), label: String::new(), detail: "request failed: curl".into() };
        eprintln!("{result:?}");
        return result;
    };
    let text = String::from_utf8_lossy(&output.stdout).to_string();
    let (payload, code) = text.rsplit_once('\n').unwrap_or((text.as_str(), ""));
    if code != "200" {
        let result = Generated { status: "error".into(), label: String::new(), detail: format!("HTTP {code}: {}", &payload.chars().take(200).collect::<String>()) };
        eprintln!("{result:?}");
        return result;
    }
    let Ok(parsed) = serde_json::from_str::<serde_json::Value>(payload) else {
        let result = Generated { status: "error".into(), label: String::new(), detail: "bad response".into() };
        eprintln!("{result:?}");
        return result;
    };
    let content = parsed.pointer("/choices/0/message/content").and_then(|value| value.as_str()).unwrap_or("").to_string();
    if content.trim().is_empty() {
        let result = Generated { status: "error".into(), label: String::new(), detail: "empty response".into() };
        eprintln!("{result:?}");
        return result;
    }
    let result = Generated { status: "ok".into(), label: content, detail: String::new() };
    eprintln!("{result:?}");
    result
}

fn clean_commit_subject(text: &str) -> String {
    let cleaned = text.replace('\r', "");
    let first = cleaned.lines().next().unwrap_or("").trim().trim_matches('"').trim_matches('\'').trim_matches('`');
    let first = first.strip_suffix('.').unwrap_or(first);
    let pattern = regex::Regex::new(r"^(?:feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(?:\([a-zA-Z0-9._/-]+\))?!?: .{1,100}$").expect("subject");
    if pattern.is_match(first) { first.to_string() } else { String::new() }
}

pub fn api_key() -> String {
    if let Some(key) = nonempty(&env::var("OPENROUTER_API_KEY").unwrap_or_default()) {
        return key;
    }
    for path in env_files() {
        if let Some(key) = key_in_file(&path) {
            return key;
        }
    }
    String::new()
}

fn env_files() -> Vec<PathBuf> {
    let mut paths = Vec::new();
    if let Some(path) = nonempty(&env::var("OPENROUTER_ENV_FILE").unwrap_or_default()) {
        paths.push(PathBuf::from(path));
    }
    let config = env::var("XDG_CONFIG_HOME").ok().filter(|value| !value.trim().is_empty()).map(PathBuf::from).or_else(|| env::var_os("HOME").map(|home| PathBuf::from(home).join(".config")));
    if let Some(config) = config {
        paths.push(config.join("linux-kitty-terminal-description/openrouter.env"));
    }
    if let Ok(exe) = env::current_exe() {
        if let Some(parent) = exe.parent() {
            paths.push(parent.join(".env"));
            if let Some(grand) = parent.parent() {
                paths.push(grand.join(".env"));
            }
        }
    }
    paths
}

fn key_in_file(path: &Path) -> Option<String> {
    let text = fs::read_to_string(path).ok()?;
    for raw in text.lines() {
        let line = raw.trim();
        if line.is_empty() || line.starts_with('#') || !line.contains('=') {
            continue;
        }
        let (mut key, value) = line.split_once('=').unwrap();
        key = key.trim().strip_prefix("export ").unwrap_or(key.trim());
        if key == "OPENROUTER_API_KEY" {
            return nonempty(value.trim().trim_matches('"').trim_matches('\''));
        }
    }
    None
}

fn nonempty(value: &str) -> Option<String> {
    let text = value.trim();
    if text.is_empty() { None } else { Some(text.to_string()) }
}

pub fn py_json(value: &str) -> String {
    let mut out = String::from("\"");
    for ch in value.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 || (c as u32) > 0x7f => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

pub fn describe_json(item: &Described) -> String {
    format!("{{\"detail\": {}, \"label\": {}, \"status\": {}, \"title\": {}}}", py_json(&item.detail), py_json(&item.label), py_json(&item.status), py_json(&item.title))
}
