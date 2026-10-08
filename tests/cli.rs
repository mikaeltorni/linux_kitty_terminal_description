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
