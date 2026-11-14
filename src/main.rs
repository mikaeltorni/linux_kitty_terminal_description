mod envfile;
mod hookcfg;
mod label;
mod summary;
mod title;

use std::env;
use std::io::{self, Read};
use std::path::Path;
use std::process::ExitCode;

fn main() -> ExitCode {
    let args: Vec<String> = env::args().skip(1).collect();
    match dispatch(&args) {
        Ok(code) => ExitCode::from(code),
        Err(message) => {
            eprintln!("{message}");
            ExitCode::from(2)
        }
    }
}

fn dispatch(args: &[String]) -> Result<u8, String> {
    let command = args.first().map(String::as_str).unwrap_or("");
    match command {
        "clean-label" => {
            println!("{}", label::clean_label(args.get(1).map(String::as_str).unwrap_or(""), 8, 64));
            Ok(0)
        }
        "describe" => describe(args),
        "title" => title_command(args),
        "completion-message" => {
            let _name = flag(args, "--project-name");
            let _path = flag(args, "--project-path");
            Ok(0)
        }
        "commit-message" => commit_message(args),
        "generate-json" => generate_json(),
        "claude-hook" => claude_hook(),
        "env-copy-key" => {
            let source = args.get(1).ok_or("usage: env-copy-key SOURCE TARGET")?;
            let target = args.get(2).ok_or("usage: env-copy-key SOURCE TARGET")?;
            envfile::copy_openrouter_key(Path::new(source), Path::new(target));
            Ok(0)
        }
        "env-set-key" => {
            let target = args.get(1).ok_or("usage: env-set-key TARGET")?;
            let mut key = String::new();
            io::stdin().read_to_string(&mut key).map_err(|err| err.to_string())?;
            if key.ends_with('\n') {
                key.pop();
            }
            if envfile::write_openrouter_key(Path::new(target), &key) { Ok(0) } else { Ok(2) }
        }
        "hook-config" => {
            let file = args.get(1).ok_or("usage: hook-config SETTINGS COMMAND")?;
            let command = args.get(2).ok_or("usage: hook-config SETTINGS COMMAND")?;
            hookcfg::configure(Path::new(file), command)?;
            Ok(0)
        }
        "hook-remove" => {
            let file = args.get(1).ok_or("usage: hook-remove SETTINGS")?;
            hookcfg::remove(Path::new(file))?;
            Ok(0)
        }
        "" => Err("usage: kitty-terminal-description COMMAND".into()),
        other => Err(format!("unknown command: {other}")),
    }
}

fn describe(args: &[String]) -> Result<u8, String> {
    let prompt = required(args, "--prompt")?;
    let project = flag(args, "--project-path");
    let item = summary::describe_prompt(&prompt, &project);
    if has_flag(args, "--json") {
        println!("{}", summary::describe_json(&item));
    } else {
        println!("{}", item.title);
    }
    Ok(0)
}

fn title_command(args: &[String]) -> Result<u8, String> {
    let prompt = required(args, "--prompt")?;
    let project = flag(args, "--project-path");
    let item = summary::describe_prompt(&prompt, &project);
    if has_flag(args, "--print-title") {
        println!("{}", item.title);
    }
    Ok(0)
}

fn commit_message(args: &[String]) -> Result<u8, String> {
    let mut diff = String::new();
    io::stdin().read_to_string(&mut diff).map_err(|err| err.to_string())?;
    if diff.trim().is_empty() {
        eprintln!("empty diff");
        return Ok(2);
    }
    let result = summary::commit_subject(&diff, &flag(args, "--project-path"));
    if result.status == "ok" {
        println!("{}", result.label);
        return Ok(0);
    }
    eprintln!("{}", if result.detail.is_empty() { result.status } else { result.detail });
    Ok(2)
}

fn generate_json() -> Result<u8, String> {
    let mut raw = String::new();
    io::stdin().read_to_string(&mut raw).map_err(|err| err.to_string())?;
    let payload: serde_json::Value = serde_json::from_str(if raw.trim().is_empty() { "{}" } else { raw.trim() }).unwrap_or(serde_json::Value::Null);
    if !payload.is_object() {
        println!("{{\"detail\": {}, \"status\": \"error\", \"text\": \"\"}}", summary::py_json("invalid request: request must be a JSON object"));
        return Ok(0);
    }
    let system = payload.get("system_prompt").and_then(|value| value.as_str()).unwrap_or("").trim();
    let user = payload.get("user_prompt").and_then(|value| value.as_str()).unwrap_or("").trim();
    let max_tokens = payload.get("max_tokens").and_then(|value| value.as_i64()).unwrap_or(65_536);
    let result = summary::generate_text(system, user, max_tokens);
    println!("{{\"detail\": {}, \"status\": {}, \"text\": {}}}", summary::py_json(&result.detail), summary::py_json(&result.status), summary::py_json(&result.label));
    Ok(0)
}

fn claude_hook() -> Result<u8, String> {
    let mut raw = String::new();
    let _ = io::stdin().read_to_string(&mut raw);
    let Ok(payload) = serde_json::from_str::<serde_json::Value>(if raw.trim().is_empty() { "{}" } else { raw.trim() }) else {
        return Ok(0);
    };
    if payload.get("hook_event_name").and_then(|value| value.as_str()) != Some("UserPromptSubmit") {
        return Ok(0);
    }
    let prompt = ["prompt", "message", "input"].iter().find_map(|key| {
        payload.get(*key).and_then(|value| value.as_str()).map(str::trim).filter(|text| !text.is_empty()).map(|text| text.split_whitespace().collect::<Vec<_>>().join(" "))
    }).unwrap_or_default();
    if prompt.is_empty() {
        return Ok(0);
    }
    let project = env::var("CODEX_AGENT_PROJECT_PATH").unwrap_or_default();
    let project = if project.trim().is_empty() { payload.get("cwd").and_then(|value| value.as_str()).unwrap_or("").to_string() } else { project };
    let _item = summary::describe_prompt(&prompt, &project);
    Ok(0)
}

fn flag(args: &[String], name: &str) -> String {
    args.iter().position(|arg| arg == name).and_then(|index| args.get(index + 1)).cloned().unwrap_or_default()
}

fn required(args: &[String], name: &str) -> Result<String, String> {
    let value = flag(args, name);
    if value.is_empty() { Err(format!("missing {name}")) } else { Ok(value) }
}

fn has_flag(args: &[String], name: &str) -> bool {
    args.iter().any(|arg| arg == name)
}
