//! Project titles composed without calling OpenRouter.

use std::path::Path;
use std::process::Command;

pub fn human_project_name(path: &str) -> String {
    let name = Path::new(path).file_name().and_then(|name| name.to_str()).filter(|name| !name.is_empty()).unwrap_or(path);
    let words = name.split(|ch| ch == '-' || ch == '_').filter(|part| !part.is_empty()).collect::<Vec<_>>().join(" ");
    let words = if words.is_empty() { name.trim().to_string() } else { words };
    let words = words.trim().to_string();
    if words.is_empty() {
        return "Project".into();
    }
    let mut chars = words.chars();
    match chars.next() {
        Some(first) => first.to_uppercase().collect::<String>() + chars.as_str(),
        None => "Project".into(),
    }
}

pub fn clean_title_prompt(prompt: &str) -> String {
    let mut text = prompt.split_whitespace().collect::<Vec<_>>().join(" ");
    if let Some(rest) = text.strip_prefix("RAW:") {
        text = rest.trim_start().to_string();
    }
    if text == "/goal" {
        text.clear();
    } else if let Some(rest) = text.strip_prefix("/goal ") {
        text = rest.trim_start().to_string();
    }
    text
}

pub fn git_worktree_name(project_path: &str) -> Option<String> {
    let path = project_path.trim();
    if path.is_empty() || path == "." {
        return None;
    }
    let output = Command::new("git").args(["-C", path, "rev-parse", "--show-toplevel"]).output().ok()?;
    if !output.status.success() {
        return None;
    }
    let toplevel = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if toplevel.is_empty() {
        return None;
    }
    Path::new(&toplevel).file_name().and_then(|name| name.to_str()).map(str::to_string)
}

pub fn project_git_context(project_path: &str) -> String {
    let label = human_project_name(project_path);
    let Some(summary) = git_worktree_name(project_path) else { return label };
    if human_project_name(&summary) == label {
        label
    } else {
        format!("{label} · {summary}")
    }
}

pub fn prompt_title(prompt: &str, project_path: &str) -> String {
    let raw = prompt.trim();
    if raw.is_empty() {
        return project_git_context(project_path);
    }
    let normalized = clean_title_prompt(raw);
    let display = if normalized.is_empty() { raw.chars().take(40).collect() } else { normalized };
    let context = project_git_context(project_path);
    if context.chars().count() >= 251 {
        let kept: String = context.chars().take(252).collect();
        return format!("{}...", kept.trim_end());
    }
    let available = 251usize.saturating_sub(context.chars().count());
    let shortened: String = display.chars().take(available).collect();
    let shortened = if display.chars().count() > available {
        format!("{}...", shortened.trim_end())
    } else {
        shortened
    };
    format!("{shortened} · {context}")
}
