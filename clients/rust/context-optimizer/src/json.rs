//! Python-compatible canonical JSON, with duplicate-key and tree bounds.
use serde::de::{self, MapAccess, Visitor};
use serde::{Deserialize, Deserializer};
use serde_json::{value::RawValue, Value};
use std::collections::HashSet;
use std::fmt;

use crate::{Error, MAX_DEPTH, MAX_NODES};

struct Entries<'a>(Vec<(String, &'a RawValue)>);
impl<'de> Deserialize<'de> for Entries<'de> {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct Object;
        impl<'de> Visitor<'de> for Object {
            type Value = Entries<'de>;
            fn expecting(&self, formatter: &mut fmt::Formatter) -> fmt::Result {
                formatter.write_str("a JSON object without duplicate keys")
            }
            fn visit_map<M: MapAccess<'de>>(self, mut map: M) -> Result<Self::Value, M::Error> {
                let mut keys = HashSet::new();
                let mut entries = Vec::new();
                while let Some(key) = map.next_key::<String>()? {
                    if !keys.insert(key.clone()) {
                        return Err(de::Error::custom("duplicate key"));
                    }
                    entries.push((key, map.next_value::<&RawValue>()?));
                }
                Ok(Entries(entries))
            }
        }
        deserializer.deserialize_map(Object)
    }
}

pub fn strict(text: &str) -> Result<Value, Error> {
    strict_bounded(text, MAX_DEPTH, MAX_NODES)
}

pub fn strict_bounded(text: &str, max_depth: usize, max_nodes: usize) -> Result<Value, Error> {
    let raw: &RawValue = serde_json::from_str(text).map_err(|_| Error::InvalidJson)?;
    parse(raw, 0, &mut 0, max_depth, max_nodes)
}

fn parse(
    raw: &RawValue,
    depth: usize,
    nodes: &mut usize,
    max_depth: usize,
    max_nodes: usize,
) -> Result<Value, Error> {
    *nodes += 1;
    if *nodes > max_nodes || depth > max_depth {
        return Err(Error::LimitExceeded);
    }
    match raw.get().as_bytes().first() {
        Some(b'{') => {
            let entries: Entries<'_> =
                serde_json::from_str(raw.get()).map_err(|_| Error::InvalidJson)?;
            let mut object = serde_json::Map::new();
            for (key, child) in entries.0 {
                object.insert(key, parse(child, depth + 1, nodes, max_depth, max_nodes)?);
            }
            // Do not deserialize arbitrary-precision Value objects: its
            // private number-tag key can coerce an ordinary JSON object into
            // a number. Construct maps directly and parse only scalar leaves.
            Ok(Value::Object(object))
        }
        Some(b'[') => {
            let entries: Vec<&RawValue> =
                serde_json::from_str(raw.get()).map_err(|_| Error::InvalidJson)?;
            entries
                .into_iter()
                .map(|child| parse(child, depth + 1, nodes, max_depth, max_nodes))
                .collect::<Result<Vec<_>, _>>()
                .map(Value::Array)
        }
        _ => serde_json::from_str(raw.get()).map_err(|_| Error::InvalidJson),
    }
}

pub fn canonical(value: &Value) -> Result<String, Error> {
    let mut output = String::new();
    write(value, &mut output)?;
    Ok(output)
}

fn write(value: &Value, output: &mut String) -> Result<(), Error> {
    match value {
        Value::Null => output.push_str("null"),
        Value::Bool(value) => output.push_str(if *value { "true" } else { "false" }),
        Value::Number(value) => {
            let raw = value.to_string();
            if raw.contains(['.', 'e', 'E']) {
                let number: f64 = raw.parse().map_err(|_| Error::InvalidJson)?;
                if !number.is_finite() {
                    return Err(Error::InvalidJson);
                }
                output.push_str(&python_float(number));
            } else {
                output.push_str(&raw);
            }
        }
        Value::String(value) => {
            output.push_str(&serde_json::to_string(value).map_err(|_| Error::InvalidJson)?)
        }
        Value::Array(values) => {
            output.push('[');
            for (index, value) in values.iter().enumerate() {
                if index != 0 {
                    output.push(',');
                }
                write(value, output)?;
            }
            output.push(']');
        }
        Value::Object(values) => {
            output.push('{');
            for (index, (key, value)) in values.iter().enumerate() {
                if index != 0 {
                    output.push(',');
                }
                output.push_str(&serde_json::to_string(key).map_err(|_| Error::InvalidJson)?);
                output.push(':');
                write(value, output)?;
            }
            output.push('}');
        }
    }
    Ok(())
}

// ryu supplies the shortest round-trip digits; Python differs in exponent
// spelling and switches to exponent notation outside [-4, 15].
fn python_float(value: f64) -> String {
    let mut buffer = ryu::Buffer::new();
    let raw = buffer.format_finite(value);
    let (sign, raw) = raw.strip_prefix('-').map_or(("", raw), |rest| ("-", rest));
    let (mantissa, power) = raw.split_once('e').map_or((raw, 0), |(mantissa, power)| {
        (mantissa, power.parse::<i32>().expect("ryu exponent"))
    });
    let point = mantissa.find('.').unwrap_or(mantissa.len()) as i32;
    let all: String = mantissa
        .chars()
        .filter(|character| *character != '.')
        .collect();
    let leading = all.bytes().position(|byte| byte != b'0');
    let Some(leading) = leading else {
        return format!("{sign}0.0");
    };
    let exponent = point + power - leading as i32 - 1;
    let digits = all[leading..].trim_end_matches('0');
    if !(-4..16).contains(&exponent) {
        let rest = &digits[1..];
        let fraction = if rest.is_empty() {
            String::new()
        } else {
            format!(".{rest}")
        };
        return format!(
            "{sign}{}{fraction}e{}{abs:02}",
            &digits[..1],
            if exponent < 0 { '-' } else { '+' },
            abs = exponent.abs()
        );
    }
    let point = exponent + 1;
    if point <= 0 {
        format!("{sign}0.{}{digits}", "0".repeat((-point) as usize))
    } else if point as usize >= digits.len() {
        format!(
            "{sign}{digits}{}.0",
            "0".repeat(point as usize - digits.len())
        )
    } else {
        format!(
            "{sign}{}.{}",
            &digits[..point as usize],
            &digits[point as usize..]
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn preserves_order_and_python_number_spelling() {
        let input = r#"{"z":1e-7,"a":1e16,"b":1e-4,"c":-0.0,"d":1000000000000000000000000000001}"#;
        assert_eq!(
            canonical(&strict(input).unwrap()).unwrap(),
            r#"{"z":1e-07,"a":1e+16,"b":0.0001,"c":-0.0,"d":1000000000000000000000000000001}"#
        );
    }
    #[test]
    fn rejects_duplicate_keys_and_bounded_trees() {
        for input in [r#"{"x":1,"x":2}"#, r#"{"a":{"x":1,"\u0078":2}}"#, "NaN"] {
            assert!(strict(input).is_err());
        }
        assert!(strict(&format!("{}0{}", "[".repeat(33), "]".repeat(33))).is_err());
        assert!(strict(&format!("[{}0]", "0,".repeat(MAX_NODES))).is_err());
    }

    #[test]
    fn arbitrary_precision_private_tags_remain_ordinary_objects() {
        for input in [
            r#"{"$serde_json::private::Number":"1"}"#,
            r#"{"nested":[{"$serde_json::private::Number":"NaN"}],"n":100000000000000000000001}"#,
        ] {
            assert_eq!(canonical(&strict(input).unwrap()).unwrap(), input);
        }
    }
}
