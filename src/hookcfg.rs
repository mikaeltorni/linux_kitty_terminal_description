//! Claude UserPromptSubmit hook registration for Kitty titles.

use serde_json::{json, Value};
use std::fs;
use std::path::Path;

pub fn configure(path: &Path, command: &str) -> Result<(), String> {
    let mut settings = read_object(path);
    let hooks = object_mut(&mut settings, "hooks");
    let entries = array_mut(hooks, "UserPromptSubmit");
    if !entries.iter().any(|entry| entry_has_command(entry, command)) {
        let mut attached = false;
        for entry in entries.iter_mut() {
            if entry.get("matcher").and_then(Value::as_str).unwrap_or("") == "" {
                if !entry.get("hooks").map(Value::is_array).unwrap_or(false) {
                    entry.as_object_mut().unwrap().insert("hooks".into(), json!([]));
                }
                entry.get_mut("hooks").and_then(Value::as_array_mut).unwrap().push(json!({"type": "command", "command": command}));
                attached = true;
                break;
            }
        }
        if !attached {
            entries.push(json!({"matcher": "", "hooks": [{"type": "command", "command": command}]}));
        }
    }
    write_pretty(path, &settings)
}

pub fn remove(path: &Path) -> Result<(), String> {
    let Ok(text) = fs::read_to_string(path) else { return Ok(()) };
    let Ok(mut settings) = serde_json::from_str::<Value>(&text) else { return Ok(()) };
    let Some(settings_obj) = settings.as_object_mut() else { return Ok(()) };
    let Some(hooks) = settings_obj.get_mut("hooks").and_then(Value::as_object_mut) else { return Ok(()) };
    if let Some(entries) = hooks.get_mut("UserPromptSubmit").and_then(Value::as_array_mut) {
        entries.retain(|entry| !entry.to_string().contains("kitty-terminal-description claude-hook"));
        if entries.is_empty() {
            hooks.remove("UserPromptSubmit");
        }
    }
    if hooks.is_empty() {
        settings_obj.remove("hooks");
    }
    write_pretty(path, &settings)
}

fn entry_has_command(entry: &Value, command: &str) -> bool {
    entry.get("hooks").and_then(Value::as_array).map(|hooks| hooks.iter().any(|hook| hook.get("command").and_then(Value::as_str) == Some(command))).unwrap_or(false)
}

fn read_object(path: &Path) -> Value {
    fs::read_to_string(path).ok().and_then(|text| serde_json::from_str(&text).ok()).filter(Value::is_object).unwrap_or_else(|| json!({}))
}

fn object_mut<'a>(value: &'a mut Value, key: &str) -> &'a mut serde_json::Map<String, Value> {
    let object = value.as_object_mut().unwrap();
    if !object.get(key).map(Value::is_object).unwrap_or(false) {
        object.insert(key.to_string(), json!({}));
    }
    object.get_mut(key).and_then(Value::as_object_mut).unwrap()
}

fn array_mut<'a>(object: &'a mut serde_json::Map<String, Value>, key: &str) -> &'a mut Vec<Value> {
    if !object.get(key).map(Value::is_array).unwrap_or(false) {
        object.insert(key.to_string(), json!([]));
    }
    object.get_mut(key).and_then(Value::as_array_mut).unwrap()
}

fn write_pretty(path: &Path, value: &Value) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|err| err.to_string())?;
    }
    let text = serde_json::to_string_pretty(value).map_err(|err| err.to_string())?;
    fs::write(path, format!("{text}\n")).map_err(|err| err.to_string())
}
