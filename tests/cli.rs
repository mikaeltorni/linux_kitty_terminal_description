//! Public checks for the Kitty description command.

use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::PathBuf;
use std::process::Command;

fn bin() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_kitty-terminal-description"))
}

fn isolated(command: &mut Command) {
    let home = std::env::temp_dir().join(format!("kitty-cli-{}", std::process::id()));
    let _ = fs::create_dir_all(&home);
    command.env("XDG_CONFIG_HOME", &home);
    command.env("OPENROUTER_ENV_FILE", home.join("missing.env"));
    command.env("KITTY_TERMINAL_DESCRIPTION_STATE_DIR", &home);
    command.env_remove("OPENROUTER_API_KEY");
    command.env_remove("CODEX_AGENT_PROJECT_PATH");
}

#[test]
fn describe_without_key_matches_raw_title_twice() {
    let mut runs = Vec::new();
    for _ in 0..2 {
        let mut command = Command::new(bin());
        isolated(&mut command);
        command.args(["describe", "--prompt", "fix the queue race", "--project-path", "/work/demo"]);
        let output = command.output().unwrap();
        assert_eq!(output.status.code(), Some(0));
        runs.push(String::from_utf8_lossy(&output.stdout).to_string());
    }
    assert_eq!(runs[0], "fix the queue race · Demo\n");
    assert_eq!(runs[0], runs[1]);
    let mut command = Command::new(bin());
    isolated(&mut command);
    command.args(["describe", "--json", "--prompt", "fix the queue race", "--project-path", "/work/demo"]);
    let output = command.output().unwrap();
    assert_eq!(
        String::from_utf8_lossy(&output.stdout).trim(),
        "{\"detail\": \"no OPENROUTER_API_KEY\", \"label\": \"\", \"status\": \"no_key\", \"title\": \"fix the queue race \\u00b7 Demo\"}"
    );
}

#[test]
fn copy_key_and_hook_config_match_python_files() {
    let dir = std::env::temp_dir().join(format!("kitty-setup-{}", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    let source = dir.join("source.env");
    let target = dir.join("target.env");
    fs::write(&source, "OTHER=1\nexport OPENROUTER_API_KEY=sk-test-value\nSECRET=no\n").unwrap();
    let output = Command::new(bin()).args(["env-copy-key", source.to_str().unwrap(), target.to_str().unwrap()]).output().unwrap();
    assert_eq!(output.status.code(), Some(0));
    assert_eq!(fs::read_to_string(&target).unwrap(), "export OPENROUTER_API_KEY=sk-test-value\n");
    assert_eq!(fs::metadata(&target).unwrap().permissions().mode() & 0o777, 0o600);
    let settings = dir.join("settings.json");
    let output = Command::new(bin()).args(["hook-config", settings.to_str().unwrap(), "kitty-terminal-description claude-hook"]).output().unwrap();
    assert_eq!(output.status.code(), Some(0), "{}", String::from_utf8_lossy(&output.stderr));
    let golden = "{\n  \"hooks\": {\n    \"UserPromptSubmit\": [\n      {\n        \"matcher\": \"\",\n        \"hooks\": [\n          {\n            \"type\": \"command\",\n            \"command\": \"kitty-terminal-description claude-hook\"\n          }\n        ]\n      }\n    ]\n  }\n}\n";
    assert_eq!(fs::read_to_string(&settings).unwrap(), golden);
    let again = Command::new(bin()).args(["hook-config", settings.to_str().unwrap(), "kitty-terminal-description claude-hook"]).output().unwrap();
    assert_eq!(again.status.code(), Some(0));
    assert_eq!(fs::read_to_string(&settings).unwrap(), golden);
    let _ = fs::remove_dir_all(dir);
}

/// Replay the logged throttle through the public JSON title command.
/// Parameters: none.
/// Returns: None.
#[test]
fn describe_routes_around_the_logged_primary_model_throttle() {
    eprintln!("parameters=none");
    let dir = std::env::temp_dir().join(format!("kitty-title-throttle-{}", std::process::id()));
    fs::create_dir_all(&dir).unwrap();
    let curl = dir.join("curl");
    // Simulate OpenRouter routing the same request to a healthy fallback.
    fs::write(&curl, r#"#!/bin/sh
while [ "$#" -gt 1 ] && [ "$1" != "--data-binary" ]; do shift; done
case "$2" in
    *'"model":"openai/gpt-4.1-nano"'*)
        printf '%s\n200' '{"choices":[{"message":{"content":"Pinned title model"}}]}'
        ;;
    *'lets add the logo to each of the bars'*'"models":["google/gemini-2.5-flash-lite","openai/gpt-4.1-nano"]'*)
        printf '%s\n200' '{"model":"openai/gpt-4.1-nano","choices":[{"message":{"content":"Logo background placement"}}]}'
        ;;
    *)
        printf '%s\n429' '{"error":{"message":"Provider returned error","code":429,"metadata":{"raw":"google/gemini-2.5-flash-lite is temporarily rate-limited upstream."}}}'
        ;;
esac
"#).unwrap();
    fs::set_permissions(&curl, fs::Permissions::from_mode(0o700)).unwrap();
    let mut paths = vec![dir.clone()];
    paths.extend(std::env::split_paths(&std::env::var_os("PATH").unwrap_or_default()));
    for (model, prompt, status, label, title) in [
        (
            None, include_str!("title_rate_limit_prompt.txt").trim(), "ok",
            "Logo background placement", "Logo background placement · Demo",
        ),
        (
            Some("openai/gpt-4.1-nano"), "Fix the queue race", "ok",
            "Pinned title model", "Pinned title model · Demo",
        ),
        (
            None, "Fix the queue race", "error",
            "", "Fix the queue race · Demo",
        ),
    ] {
        let mut command = Command::new(bin());
        isolated(&mut command);
        if let Some(model) = model {
            command.env("OPENROUTER_SUMMARY_MODEL", model);
        } else {
            command.env_remove("OPENROUTER_SUMMARY_MODEL");
        }
        let output = command
            .env("PATH", std::env::join_paths(&paths).unwrap())
            .env("OPENROUTER_API_KEY", "test-key")
            .args(["describe", "--json", "--prompt", prompt, "--project-path", "/work/demo"])
            .output()
            .unwrap();
        assert!(output.status.success(), "{:?}", output);
        let result: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
        assert_eq!(result["status"], status, "{result}");
        assert_eq!(result["label"], label);
        assert_eq!(result["title"], title);
    }
    fs::remove_dir_all(dir).unwrap();
    eprintln!("None");
}
