//! Copy or store only OPENROUTER_API_KEY.

use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::Path;

fn env_key(line: &str) -> String {
    let stripped = line.trim();
    let mut key = stripped.split_once('=').map(|(key, _)| key.trim()).unwrap_or("");
    if let Some(rest) = key.strip_prefix("export ") {
        key = rest.trim();
    }
    key.to_string()
}

pub fn copy_openrouter_key(source: &Path, target: &Path) -> bool {
    let Ok(text) = fs::read_to_string(source) else { return false };
    let mut key_line = "";
    for raw in text.lines() {
        let stripped = raw.trim();
        if stripped.is_empty() || stripped.starts_with('#') || !stripped.contains('=') {
            continue;
        }
        if env_key(stripped) == "OPENROUTER_API_KEY" {
            key_line = stripped;
            break;
        }
    }
    if key_line.is_empty() {
        return false;
    }
    write_lines(target, key_line)
}

pub fn write_openrouter_key(target: &Path, key: &str) -> bool {
    if key.is_empty() || key.contains('\n') || key.contains('\r') {
        return false;
    }
    write_lines(target, &format!("OPENROUTER_API_KEY={key}"))
}

fn write_lines(target: &Path, key_line: &str) -> bool {
    let mut lines = Vec::new();
    if let Ok(existing) = fs::read_to_string(target) {
        for raw in existing.lines() {
            if env_key(raw) != "OPENROUTER_API_KEY" {
                lines.push(raw.to_string());
            }
        }
    }
    lines.push(key_line.to_string());
    if let Some(parent) = target.parent() {
        if fs::create_dir_all(parent).is_err() {
            return false;
        }
    }
    let body = format!("{}\n", lines.join("\n").trim_end());
    if fs::write(target, body).is_err() {
        return false;
    }
    let _ = fs::set_permissions(target, fs::Permissions::from_mode(0o600));
    true
}
