use crate::{json, Error, Format, MAX_BLOCK_BYTES, MAX_COLUMNS, MAX_ROWS};
use serde_json::{json, Map, Value};

pub fn compact_text(text: &str, format: Format) -> String {
    if text.len() > MAX_BLOCK_BYTES || matches!(restore_text(text), Ok(Some(_)) | Err(_)) {
        return text.to_owned();
    }
    let candidate = match format {
        Format::JsonTable => json_table(text),
        Format::LineRuns => line_runs(text),
        Format::SearchLines | Format::PathList => lines(text, format),
    };
    let Some(candidate) = candidate.and_then(|value| json::canonical(&value).ok()) else {
        return text.to_owned();
    };
    if candidate.len() >= text.len() || restore_text(&candidate) != Ok(Some(text.to_owned())) {
        text.to_owned()
    } else {
        candidate
    }
}

fn json_table(text: &str) -> Option<Value> {
    let value = json::strict(text).ok()?;
    if json::canonical(&value).ok()? != text {
        return None;
    }
    let rows = value.as_array()?;
    if !(2..=MAX_ROWS).contains(&rows.len()) {
        return None;
    }
    let columns: Vec<_> = rows.first()?.as_object()?.keys().cloned().collect();
    if !(1..=MAX_COLUMNS).contains(&columns.len()) {
        return None;
    }
    let mut cells = Vec::new();
    for row in rows {
        let row = row.as_object()?;
        if !row.keys().eq(columns.iter()) {
            return None;
        }
        cells.push(Value::Array(row.values().cloned().collect()));
    }
    Some(json!({"format":"hormuz-json-table-v1", "columns":columns, "rows":cells}))
}

fn line_runs(text: &str) -> Option<Value> {
    let lines: Vec<_> = text.split('\n').collect();
    if !(2..=MAX_ROWS).contains(&lines.len()) {
        return None;
    }
    let mut runs: Vec<(&str, usize)> = Vec::new();
    for line in &lines {
        match runs.last_mut() {
            Some((last, count)) if last == line => *count += 1,
            _ => runs.push((line, 1)),
        }
    }
    (runs.len() != lines.len()).then(|| json!({"format":"hormuz-line-runs-v1", "runs":runs}))
}

fn part(line: &str, format: Format) -> Option<(String, Value)> {
    if line.contains(['\r', '\n']) {
        return None;
    }
    match format {
        Format::PathList => {
            let (parent, suffix) = line.rsplit_once('/')?;
            if parent.is_empty() || suffix.is_empty() {
                return None;
            }
            Some((format!("{parent}/"), json!(suffix)))
        }
        Format::SearchLines => {
            let mut parts = line.splitn(3, ':');
            let path = parts.next()?;
            let number = parts.next()?;
            let value = parts.next()?;
            if path.is_empty()
                || number.is_empty()
                || !number.bytes().all(|byte| byte.is_ascii_digit())
            {
                return None;
            }
            Some((path.to_owned(), json!([number, value])))
        }
        _ => None,
    }
}

fn lines(text: &str, format: Format) -> Option<Value> {
    if text.contains('\r') {
        return None;
    }
    let trailing = text.ends_with('\n');
    let plain: Vec<_> = text
        .strip_suffix('\n')
        .unwrap_or(text)
        .split('\n')
        .collect();
    if (2..=MAX_ROWS).contains(&plain.len()) {
        let parts: Option<Vec<_>> = plain.iter().map(|line| part(line, format)).collect();
        if let Some(parts) = parts {
            if parts.iter().all(|item| item.0 == parts[0].0) {
                return Some(envelope(format, &parts, None, trailing));
            }
        }
    }
    let raw: Vec<_> = text.split_inclusive('\n').collect();
    if !(2..=MAX_ROWS).contains(&raw.len()) {
        return None;
    }
    let parts: Vec<_> = raw
        .iter()
        .map(|line| part(line.strip_suffix('\n').unwrap_or(line), format))
        .collect();
    let mut best = None;
    let mut best_score = 0;
    let mut start = 0;
    while start < parts.len() {
        let Some(first) = &parts[start] else {
            start += 1;
            continue;
        };
        let mut end = start + 1;
        while end < parts.len() && parts[end].as_ref().is_some_and(|item| item.0 == first.0) {
            end += 1;
        }
        let score = first.0.len() * (end - start - 1);
        if end - start >= 2 && score > best_score {
            best = Some((start, end));
            best_score = score;
        }
        start = end;
    }
    let (start, end) = best?;
    let before = raw[..start].concat();
    let after = format!(
        "{}{}",
        if raw[end - 1].ends_with('\n') {
            "\n"
        } else {
            ""
        },
        raw[end..].concat()
    );
    if before.is_empty() && after.is_empty() {
        return None;
    }
    let selected: Vec<_> = parts[start..end].iter().cloned().collect::<Option<_>>()?;
    Some(envelope(format, &selected, Some((before, after)), false))
}

fn envelope(
    format: Format,
    parts: &[(String, Value)],
    frame: Option<(String, String)>,
    trailing: bool,
) -> Value {
    let (marker, prefix, values) = if format == Format::PathList {
        ("hormuz-path-list-v1", "prefix", "suffixes")
    } else {
        ("hormuz-search-lines-v1", "path", "matches")
    };
    let mut object = Map::new();
    object.insert("format".into(), json!(marker));
    if let Some((before, after)) = frame {
        object.insert("before".into(), json!(before));
        object.insert(prefix.into(), json!(parts[0].0));
        object.insert(
            values.into(),
            json!(parts.iter().map(|item| item.1.clone()).collect::<Vec<_>>()),
        );
        object.insert("after".into(), json!(after));
    } else {
        object.insert(prefix.into(), json!(parts[0].0));
        object.insert(
            values.into(),
            json!(parts.iter().map(|item| item.1.clone()).collect::<Vec<_>>()),
        );
        object.insert("trailing_newline".into(), json!(trailing));
    }
    Value::Object(object)
}

pub fn restore_text(text: &str) -> Result<Option<String>, Error> {
    let value = match json::strict(text) {
        Ok(value) => value,
        Err(_) => {
            // A malformed declaration must not be wrapped in a new envelope.
            static DECLARED: std::sync::LazyLock<regex::Regex> = std::sync::LazyLock::new(|| {
                regex::Regex::new(r#"(?s)^\s*\{.*"format"\s*:\s*"(?:hormuz-json-table-v1|hormuz-line-runs-v1|hormuz-search-lines-v1|hormuz-path-list-v1)""#).expect("fixed marker pattern")
            });
            return if DECLARED.is_match(text) {
                Err(Error::MalformedEnvelope)
            } else {
                Ok(None)
            };
        }
    };
    let Some(object) = value.as_object() else {
        return Ok(None);
    };
    let marker = object.get("format").and_then(Value::as_str).unwrap_or("");
    if ![
        "hormuz-json-table-v1",
        "hormuz-line-runs-v1",
        "hormuz-search-lines-v1",
        "hormuz-path-list-v1",
    ]
    .contains(&marker)
    {
        return Ok(None);
    }
    if json::canonical(&value)? != text {
        return Err(Error::MalformedEnvelope);
    }
    let invalid = || Error::MalformedEnvelope;
    let output = match marker {
        "hormuz-json-table-v1" => {
            keys(object, &["format", "columns", "rows"])?;
            let columns = object["columns"].as_array().ok_or_else(invalid)?;
            let rows = object["rows"].as_array().ok_or_else(invalid)?;
            if !(1..=MAX_COLUMNS).contains(&columns.len()) || !(2..=MAX_ROWS).contains(&rows.len())
            {
                return Err(invalid());
            }
            let names: Option<Vec<_>> = columns.iter().map(Value::as_str).collect();
            let names = names.ok_or_else(invalid)?;
            if names.iter().any(|name| name.is_empty())
                || names.iter().collect::<std::collections::HashSet<_>>().len() != names.len()
            {
                return Err(invalid());
            }
            let mut restored = Vec::new();
            let mut bytes = 2;
            for row in rows {
                let cells = row.as_array().ok_or_else(invalid)?;
                if cells.len() != names.len() {
                    return Err(invalid());
                }
                let row: Map<_, _> = names
                    .iter()
                    .zip(cells)
                    .map(|(name, value)| ((*name).to_owned(), value.clone()))
                    .collect();
                bytes += json::canonical(&Value::Object(row.clone()))?.len()
                    + usize::from(!restored.is_empty());
                if bytes > MAX_BLOCK_BYTES {
                    return Err(Error::LimitExceeded);
                }
                restored.push(Value::Object(row));
            }
            json::canonical(&Value::Array(restored))?
        }
        "hormuz-line-runs-v1" => {
            keys(object, &["format", "runs"])?;
            let runs = object["runs"].as_array().ok_or_else(invalid)?;
            if !(1..=MAX_ROWS).contains(&runs.len()) {
                return Err(invalid());
            }
            let mut lines = Vec::new();
            let mut bytes = 0;
            for run in runs {
                let pair = run.as_array().ok_or_else(invalid)?;
                if pair.len() != 2 {
                    return Err(invalid());
                }
                let line = pair[0].as_str().ok_or_else(invalid)?;
                let count = pair[1]
                    .as_u64()
                    .filter(|count| *count > 0 && *count <= MAX_ROWS as u64)
                    .ok_or_else(invalid)? as usize;
                if lines.len() + count > MAX_ROWS {
                    return Err(invalid());
                }
                bytes += (line.len() + 1) * count;
                if bytes > MAX_BLOCK_BYTES + 1 {
                    return Err(invalid());
                }
                lines.extend(std::iter::repeat_n(line, count));
            }
            lines.join("\n")
        }
        _ => {
            let path_list = marker == "hormuz-path-list-v1";
            let (prefix_key, values_key) = if path_list {
                ("prefix", "suffixes")
            } else {
                ("path", "matches")
            };
            let legacy = object.len() == 4 && object.contains_key("trailing_newline");
            let (before, after) = if legacy {
                keys(
                    object,
                    &["format", prefix_key, values_key, "trailing_newline"],
                )?;
                (
                    "",
                    if object["trailing_newline"].as_bool().ok_or_else(invalid)? {
                        "\n"
                    } else {
                        ""
                    },
                )
            } else {
                keys(
                    object,
                    &["format", "before", prefix_key, values_key, "after"],
                )?;
                let frame = (
                    object["before"].as_str().ok_or_else(invalid)?,
                    object["after"].as_str().ok_or_else(invalid)?,
                );
                if frame.0.is_empty() && frame.1.is_empty() {
                    return Err(invalid());
                }
                frame
            };
            let prefix = object[prefix_key].as_str().ok_or_else(invalid)?;
            if prefix.is_empty()
                || prefix.contains(['\r', '\n'])
                || (path_list && !prefix.ends_with('/'))
                || (!path_list && prefix.contains(':'))
            {
                return Err(invalid());
            }
            let values = object[values_key].as_array().ok_or_else(invalid)?;
            if !(2..=MAX_ROWS).contains(&values.len()) {
                return Err(invalid());
            }
            let mut lines = Vec::new();
            let mut bytes = before.len() + after.len();
            if bytes > MAX_BLOCK_BYTES {
                return Err(Error::LimitExceeded);
            }
            for value in values {
                bytes += usize::from(!lines.is_empty());
                if path_list {
                    let suffix = value.as_str().ok_or_else(invalid)?;
                    if suffix.is_empty() || suffix.contains(['/', '\r', '\n']) {
                        return Err(invalid());
                    }
                    bytes += prefix.len() + suffix.len();
                    if bytes > MAX_BLOCK_BYTES {
                        return Err(Error::LimitExceeded);
                    }
                    lines.push(format!("{prefix}{suffix}"));
                } else {
                    let pair = value.as_array().ok_or_else(invalid)?;
                    if pair.len() != 2 {
                        return Err(invalid());
                    }
                    let number = pair[0].as_str().ok_or_else(invalid)?;
                    let content = pair[1].as_str().ok_or_else(invalid)?;
                    if number.is_empty()
                        || !number.chars().all(is_digit)
                        || content.contains(['\r', '\n'])
                    {
                        return Err(invalid());
                    }
                    bytes += prefix.len() + number.len() + content.len() + 2;
                    if bytes > MAX_BLOCK_BYTES {
                        return Err(Error::LimitExceeded);
                    }
                    lines.push(format!("{prefix}:{number}:{content}"));
                }
            }
            format!("{before}{}{after}", lines.join("\n"))
        }
    };
    if output.len() > MAX_BLOCK_BYTES {
        return Err(Error::LimitExceeded);
    }
    Ok(Some(output))
}

fn is_digit(character: char) -> bool {
    // Python str.isdigit accepts decimal digits and compatibility digits,
    // but not fractions or Roman numerals (char::is_numeric is too broad).
    use unicode_general_category::{get_general_category, GeneralCategory};
    let code = character as u32;
    // The packaged Python 3.12 reference uses Unicode 15.0. Our maintained
    // category library uses Unicode 16.0; its newly assigned decimal digits
    // must not silently extend the structural-v1 decoder contract.
    let added_in_unicode_16 = matches!(code,
        0x10D40..=0x10D49 | 0x116D0..=0x116E3 | 0x11BF0..=0x11BF9 |
        0x16130..=0x16139 | 0x16D70..=0x16D79 | 0x1CCF0..=0x1CCF9 |
        0x1E5F1..=0x1E5FA);
    (get_general_category(character) == GeneralCategory::DecimalNumber && !added_in_unicode_16)
        || matches!(character as u32,
        0x00B2..=0x00B3 | 0x00B9 | 0x1369..=0x1371 | 0x19DA | 0x2070 | 0x2074..=0x2079 |
        0x2080..=0x2089 | 0x2460..=0x2468 | 0x2474..=0x247C | 0x2488..=0x2490 |
        0x24EA | 0x24F5..=0x24FD | 0x24FF | 0x2776..=0x277E | 0x2780..=0x2788 |
        0x278A..=0x2792 | 0x10A40..=0x10A43 | 0x10E60..=0x10E68 | 0x11052..=0x1105A |
        0x1F100..=0x1F10A)
}

fn keys(object: &Map<String, Value>, expected: &[&str]) -> Result<(), Error> {
    if object.len() != expected.len() || expected.iter().any(|key| !object.contains_key(*key)) {
        Err(Error::MalformedEnvelope)
    } else {
        Ok(())
    }
}
