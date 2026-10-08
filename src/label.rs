//! Normalize model text into a short feature label.

use regex::Regex;
use std::sync::OnceLock;

fn think_block() -> &'static Regex {
    static PATTERN: OnceLock<Regex> = OnceLock::new();
    PATTERN.get_or_init(|| Regex::new(r"(?is)<think>.*?</think>").expect("think block"))
}

fn think_tail() -> &'static Regex {
    static PATTERN: OnceLock<Regex> = OnceLock::new();
    PATTERN.get_or_init(|| Regex::new(r"(?is)<think>.*$").expect("think tail"))
}

fn think_tag() -> &'static Regex {
    static PATTERN: OnceLock<Regex> = OnceLock::new();
    PATTERN.get_or_init(|| Regex::new(r"(?i)</?think>").expect("think tag"))
}

fn punctuation() -> &'static Regex {
    static PATTERN: OnceLock<Regex> = OnceLock::new();
    PATTERN.get_or_init(|| Regex::new(r#"[\"'`*_#]"#).expect("punctuation"))
}

fn trailing_separators() -> &'static Regex {
    static PATTERN: OnceLock<Regex> = OnceLock::new();
    PATTERN.get_or_init(|| Regex::new(r"[.;:,]+$").expect("trailing separators"))
}

/// Normalize raw model output into a terse feature label.
pub fn clean_label(text: &str, max_words: i32, max_chars: usize) -> String {
    if text.is_empty() {
        return String::new();
    }
    let mut cleaned = think_block().replace_all(text, " ").into_owned();
    cleaned = think_tail().replace_all(&cleaned, " ").into_owned();
    cleaned = think_tag().replace_all(&cleaned, " ").into_owned();
    let first_line = cleaned
        .trim()
        .lines()
        .next()
        .unwrap_or("")
        .trim()
        .trim_matches('"')
        .trim_matches('\'')
        .trim();
    let stripped = trailing_separators()
        .replace(&punctuation().replace_all(first_line, ""), "")
        .into_owned();
    let mut words: Vec<&str> = stripped.split_whitespace().collect();
    if max_words > 0 {
        let limit = max_words as usize;
        if words.len() > limit {
            words.truncate(limit);
        }
    }
    let mut label = words.join(" ");
    label = label.trim().to_string();
    if label.chars().count() > max_chars {
        let truncated: String = label.chars().take(max_chars).collect();
        label = truncated
            .rsplit_once(' ')
            .map(|(head, _)| head.trim().to_string())
            .filter(|head| !head.is_empty())
            .unwrap_or_else(|| truncated.trim().to_string());
    }
    label
}

#[cfg(test)]
mod tests {
    use super::clean_label;

    #[test]
    fn strips_quotes_reasoning_and_caps_words() {
        assert_eq!(
            clean_label("\"Persistent dark mode toggle.\"", 8, 64),
            "Persistent dark mode toggle"
        );
        assert_eq!(
            clean_label("<think>notes</think>Fix queue race", 8, 64),
            "Fix queue race"
        );
        assert_eq!(clean_label("one two three four five", 3, 64), "one two three");
    }

    #[test]
    fn empty_input_stays_empty() {
        assert_eq!(clean_label("", 8, 64), "");
    }
}
