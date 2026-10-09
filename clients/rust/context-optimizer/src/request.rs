use crate::{
    compact_text, json, Counts, Error, Format, Outcome, Protocol, Selection, TokenCounter,
    MAX_BLOCK_BYTES, MAX_REQUEST_BYTES, MAX_SELECTIONS, MIN_BLOCK_BYTES, TRANSFORM_VERSION,
};
use serde_json::Value;
use std::collections::{BTreeMap, HashSet};

#[derive(Clone)]
struct Target {
    id: String,
    message: usize,
    block: Option<usize>,
    field: &'static str,
}
type Calls = BTreeMap<String, Vec<String>>;

pub fn optimize_request(
    payload: &Value,
    protocol: Protocol,
    selections: &[Selection],
    counters: Option<&dyn TokenCounter>,
    enabled: bool,
) -> Result<Outcome, Error> {
    if !enabled {
        return Ok(unchanged(payload, "disabled", None, true));
    }
    let mut selected = BTreeMap::new();
    if selections.len() > MAX_SELECTIONS {
        return Err(Error::InvalidSelection);
    }
    for selection in selections {
        if !(1..=128).contains(&selection.result_id.chars().count())
            || selected
                .insert(&selection.result_id, selection.format)
                .is_some()
        {
            return Err(Error::InvalidSelection);
        }
    }
    let Some(counters) = counters else {
        return Ok(unchanged(payload, "counter_unavailable", None, true));
    };
    let before = match json::canonical(payload) {
        Ok(value) if value.len() <= MAX_REQUEST_BYTES && json::strict(&value).is_ok() => value,
        _ => return Ok(unchanged(payload, "limit_exceeded", None, false)),
    };
    if protocol == Protocol::Responses && payload.get("previous_response_id").is_some() {
        return Ok(unchanged(payload, "unsupported_history", None, true));
    }
    let Some((targets, calls)) = targets_and_calls(payload, protocol) else {
        return Ok(unchanged(payload, "unsupported_shape", None, true));
    };
    let mut result_counts = BTreeMap::new();
    for target in &targets {
        *result_counts.entry(&target.id).or_insert(0) += 1;
    }
    let mut changed = payload.clone();
    let mut changed_blocks = 0;
    let mut eligible = false;
    for target in &targets {
        let Some(format) = selected.get(&target.id) else {
            continue;
        };
        if result_counts[&target.id] != 1 || calls.get(&target.id).map(Vec::len) != Some(1) {
            continue;
        }
        eligible = true;
        let item = target_item_mut(&mut changed, protocol, target);
        let text = item[target.field]
            .as_str()
            .expect("validated string target")
            .to_owned();
        if !(MIN_BLOCK_BYTES..=MAX_BLOCK_BYTES).contains(&text.len()) {
            continue;
        }
        let candidate = compact_text(&text, *format);
        if candidate == text {
            continue;
        }
        let old = json::canonical(item)?;
        item[target.field] = Value::String(candidate);
        let new = json::canonical(item)?;
        let (before_counts, after_counts) = match (counters.count(&old), counters.count(&new)) {
            (Ok(before), Ok(after)) if !before.is_empty() && before.keys().eq(after.keys()) => {
                (before, after)
            }
            _ => return Ok(unchanged(payload, "counter_unavailable", None, true)),
        };
        if !saves(&old, &new, &before_counts, &after_counts) {
            item[target.field] = Value::String(text);
            continue;
        }
        changed_blocks += 1;
    }
    if changed_blocks == 0 {
        return Ok(unchanged(
            payload,
            if eligible {
                "no_savings"
            } else {
                "no_eligible_result"
            },
            Some(counters),
            true,
        ));
    }
    let after = json::canonical(&changed)?;
    let (before_tokens, after_tokens) = match (counters.count(&before), counters.count(&after)) {
        (Ok(before), Ok(after)) => (before, after),
        _ => return Ok(unchanged(payload, "counter_unavailable", None, true)),
    };
    Ok(Outcome {
        payload: changed,
        changed: true,
        reason: "compacted",
        changed_blocks,
        before_bytes: Some(before.len()),
        after_bytes: Some(after.len()),
        before_tokens,
        after_tokens,
        transform_version: TRANSFORM_VERSION,
    })
}

fn unchanged(
    payload: &Value,
    reason: &'static str,
    counters: Option<&dyn TokenCounter>,
    measured: bool,
) -> Outcome {
    let text = if measured {
        json::canonical(payload).ok()
    } else {
        None
    };
    let bytes = text.as_ref().map(String::len);
    let counts = text
        .and_then(|text| counters.and_then(|counter| counter.count(&text).ok()))
        .unwrap_or_default();
    Outcome {
        payload: payload.clone(),
        changed: false,
        reason,
        changed_blocks: 0,
        before_bytes: bytes,
        after_bytes: bytes,
        before_tokens: counts.clone(),
        after_tokens: counts,
        transform_version: TRANSFORM_VERSION,
    }
}

fn saves(before: &str, after: &str, before_counts: &Counts, after_counts: &Counts) -> bool {
    after.len() <= before.len()
        && before_counts.iter().all(|(name, count)| {
            count
                .checked_sub(after_counts[name])
                .is_some_and(|saved| saved >= 32 && saved as u128 * 100 >= *count as u128 * 5)
        })
}

fn key(protocol: Protocol) -> &'static str {
    if protocol == Protocol::Responses {
        "input"
    } else {
        "messages"
    }
}
fn targets_and_calls(payload: &Value, protocol: Protocol) -> Option<(Vec<Target>, Calls)> {
    let items = payload.get(key(protocol))?.as_array()?;
    let mut targets = Vec::new();
    let mut calls = Calls::new();
    for (index, item) in items.iter().enumerate() {
        item.as_object()?;
        match protocol {
            Protocol::Responses => match item.get("type").and_then(Value::as_str) {
                Some("function_call") => call(&mut calls, item.get("call_id"), item.get("name")),
                Some("function_call_output") => target(
                    &mut targets,
                    item,
                    item.get("call_id"),
                    index,
                    None,
                    "output",
                ),
                _ => {}
            },
            Protocol::Chat => match item.get("role").and_then(Value::as_str) {
                Some("assistant") if item.get("tool_calls").is_some() => {
                    for item in item["tool_calls"].as_array()? {
                        item.as_object()?;
                        if let Some(function) =
                            item.get("function").filter(|value| value.is_object())
                        {
                            call(&mut calls, item.get("id"), function.get("name"));
                        }
                    }
                }
                Some("tool") => target(
                    &mut targets,
                    item,
                    item.get("tool_call_id"),
                    index,
                    None,
                    "content",
                ),
                _ => {}
            },
            Protocol::Anthropic => {
                let Some(blocks) = item.get("content").and_then(Value::as_array) else {
                    continue;
                };
                let role = item.get("role").and_then(Value::as_str);
                if !matches!(role, Some("assistant" | "user")) {
                    continue;
                }
                for (block_index, block) in blocks.iter().enumerate() {
                    block.as_object()?;
                    if role == Some("assistant")
                        && block.get("type").and_then(Value::as_str) == Some("tool_use")
                    {
                        call(&mut calls, block.get("id"), block.get("name"));
                    } else if role == Some("user")
                        && block.get("type").and_then(Value::as_str) == Some("tool_result")
                    {
                        target(
                            &mut targets,
                            block,
                            block.get("tool_use_id"),
                            index,
                            Some(block_index),
                            "content",
                        );
                    }
                }
            }
        }
    }
    Some((targets, calls))
}

fn call(calls: &mut Calls, id: Option<&Value>, name: Option<&Value>) {
    if let (Some(id), Some(name)) = (id.and_then(Value::as_str), name.and_then(Value::as_str)) {
        calls
            .entry(id.to_owned())
            .or_default()
            .push(name.to_owned());
    }
}
fn target(
    targets: &mut Vec<Target>,
    item: &Value,
    id: Option<&Value>,
    message: usize,
    block: Option<usize>,
    field: &'static str,
) {
    if let Some(id) = id
        .and_then(Value::as_str)
        .filter(|_| item.get(field).is_some_and(Value::is_string))
    {
        targets.push(Target {
            id: id.to_owned(),
            message,
            block,
            field,
        });
    }
}
fn target_item_mut<'a>(
    payload: &'a mut Value,
    protocol: Protocol,
    target: &Target,
) -> &'a mut Value {
    let message = &mut payload[key(protocol)][target.message];
    match target.block {
        Some(index) => &mut message["content"][index],
        None => message,
    }
}

/// Exact first-party mappings only; caller-selected offline formats use the
/// separate selections argument. Tool content never supplies configuration.
pub fn derive_selections(payload: &Value, protocol: Protocol, client: &str) -> Vec<Selection> {
    let Some(items) = payload.get(key(protocol)).and_then(Value::as_array) else {
        return Vec::new();
    };
    let mut candidates = Vec::new();
    for item in items {
        if !item.is_object() {
            return Vec::new();
        }
        match (client, protocol) {
            ("codex", Protocol::Responses)
                if item.get("type").and_then(Value::as_str) == Some("function_call") =>
            {
                let arguments = item
                    .get("arguments")
                    .and_then(Value::as_str)
                    // This is a serialized tool argument, not the governing
                    // request JSON. Match Python json.loads' last-key-wins
                    // behavior; serde's recursion limit still fails closed.
                    .and_then(|text| serde_json::from_str::<Value>(text).ok());
                if item.get("name").and_then(Value::as_str) == Some("exec_command") {
                    if let Some(arguments) = arguments.filter(|value| {
                        allowed(
                            value,
                            &[
                                "cmd",
                                "workdir",
                                "yield_time_ms",
                                "max_output_tokens",
                                "tty",
                                "login",
                            ],
                        )
                    }) {
                        if let Some(format) = arguments
                            .get("cmd")
                            .and_then(Value::as_str)
                            .and_then(command_format)
                        {
                            candidates.push((item.get("call_id"), format));
                        }
                    }
                }
            }
            ("claude-code", Protocol::Anthropic)
                if item.get("role").and_then(Value::as_str) == Some("assistant") =>
            {
                let Some(blocks) = item.get("content").and_then(Value::as_array) else {
                    continue;
                };
                for block in blocks {
                    if !block.is_object() {
                        return Vec::new();
                    }
                    if block.get("type").and_then(Value::as_str) != Some("tool_use") {
                        continue;
                    }
                    let format = match block.get("name").and_then(Value::as_str) {
                        Some("Glob") => Some(Format::PathList),
                        Some("Bash") => block
                            .get("input")
                            .filter(|value| {
                                allowed(
                                    value,
                                    &["command", "description", "timeout", "run_in_background"],
                                )
                            })
                            .and_then(|value| value.get("command"))
                            .and_then(Value::as_str)
                            .and_then(command_format),
                        _ => None,
                    };
                    if let Some(format) = format {
                        candidates.push((block.get("id"), format));
                    }
                }
            }
            _ => {}
        }
    }
    let mut seen = HashSet::new();
    candidates
        .into_iter()
        .filter_map(|(id, format)| {
            let id = id?.as_str()?;
            seen.insert(id.to_owned()).then(|| Selection {
                result_id: id.to_owned(),
                format,
            })
        })
        .collect()
}

fn allowed(value: &Value, fields: &[&str]) -> bool {
    value
        .as_object()
        .is_some_and(|object| object.keys().all(|key| fields.contains(&key.as_str())))
}
fn command_format(command: &str) -> Option<Format> {
    if command.is_empty()
        || command.len() > 4096
        || ["\n", "\r", ";", "|", "&", "<", ">", "`", "$("]
            .iter()
            .any(|marker| command.contains(marker))
    {
        return None;
    }
    // Python shlex.split disables comments. The Rust library always treats '#'
    // as a comment marker. Neutralize it for this fixed-token recognition only;
    // the parsed words are never executed and '#' cannot occur in our tokens.
    let words = shlex::split(&command.replace('#', "x"))?;
    if words.first().map(String::as_str) != Some("rg") {
        return None;
    }
    if words.iter().any(|word| word == "--files") {
        Some(Format::PathList)
    } else if words
        .iter()
        .any(|word| word == "-n" || word == "--line-number")
    {
        Some(Format::SearchLines)
    } else {
        None
    }
}
