//! Synthetic qualification interface, deliberately not a shipping integration.
use hormuz_context_optimizer::{
    compact_text, derive_selections, json, optimize_request, restore_text, validate_resources,
    Error, Format, Protocol, Selection, TokenCounter, Tokenizers, MAX_REQUEST_BYTES,
};
use serde::de::DeserializeOwned;
use serde_json::{json, Map, Value};
use std::{
    io::{self, Read},
    path::PathBuf,
    process::ExitCode,
};

enum Job {
    CompactText {
        text: String,
        format: Format,
    },
    RestoreText {
        text: String,
    },
    CountTokens {
        text: String,
    },
    OptimizeRequest {
        payload: Value,
        protocol: Protocol,
        selections: Vec<Selection>,
        enabled: bool,
    },
    DeriveSelections {
        payload: Value,
        protocol: Protocol,
        client: String,
    },
    CanonicalJson {
        value: Value,
    },
}

impl Job {
    fn parse(value: Value) -> Result<Self, Error> {
        let Value::Object(mut fields) = value else {
            return Err(Error::InvalidJson);
        };
        let operation: String = take(&mut fields, "operation")?;
        // Preserve dynamic payloads from the bounded parser. Passing them
        // through Value::deserialize would reintroduce private-tag coercion.
        let job = match operation.as_str() {
            "compact_text" => Self::CompactText {
                text: take(&mut fields, "text")?,
                format: take(&mut fields, "format")?,
            },
            "restore_text" => Self::RestoreText {
                text: take(&mut fields, "text")?,
            },
            "count_tokens" => Self::CountTokens {
                text: take(&mut fields, "text")?,
            },
            "optimize_request" => Self::OptimizeRequest {
                payload: fields.remove("payload").ok_or(Error::InvalidJson)?,
                protocol: take(&mut fields, "protocol")?,
                selections: take(&mut fields, "selections")?,
                enabled: take(&mut fields, "enabled")?,
            },
            "derive_selections" => Self::DeriveSelections {
                payload: fields.remove("payload").ok_or(Error::InvalidJson)?,
                protocol: take(&mut fields, "protocol")?,
                client: take(&mut fields, "client")?,
            },
            "canonical_json" => Self::CanonicalJson {
                value: fields.remove("value").ok_or(Error::InvalidJson)?,
            },
            _ => return Err(Error::InvalidJson),
        };
        if !fields.is_empty() {
            return Err(Error::InvalidJson);
        }
        Ok(job)
    }
}

fn take<T: DeserializeOwned>(fields: &mut Map<String, Value>, name: &str) -> Result<T, Error> {
    serde_json::from_value(fields.remove(name).ok_or(Error::InvalidJson)?)
        .map_err(|_| Error::InvalidJson)
}

fn execute() -> Result<Value, Error> {
    let arguments: Vec<_> = std::env::args_os().skip(1).collect();
    let cache = match arguments.as_slice() {
        [command, flag, cache] if command == "qualify" && flag == "--tokenizer-cache" => {
            Some(PathBuf::from(cache))
        }
        [command] if command == "qualify" => None,
        [command, flag, cache] if command == "resources" && flag == "--check" => {
            validate_resources(&PathBuf::from(cache))?;
            return Ok(json!({"resources":"ready", "tokenizers_initialized":false}));
        }
        _ => return Err(Error::InvalidJson),
    };
    let mut input = Vec::new();
    io::stdin()
        .take((2 * MAX_REQUEST_BYTES + 1) as u64)
        .read_to_end(&mut input)
        .map_err(|_| Error::InvalidJson)?;
    if input.len() > 2 * MAX_REQUEST_BYTES {
        return Err(Error::LimitExceeded);
    }
    let input = std::str::from_utf8(&input).map_err(|_| Error::InvalidJson)?;
    // Reject duplicate fields and adversarial trees before typed dispatch.
    // The qualification wrapper is not part of the request's tree budget.
    let value = json::strict_bounded(
        input,
        hormuz_context_optimizer::MAX_DEPTH + 2,
        hormuz_context_optimizer::MAX_NODES + 512,
    )?;
    let job = Job::parse(value)?;
    let load = || Tokenizers::load(cache.as_deref().ok_or(Error::ResourcesUnavailable)?);
    match job {
        Job::CompactText { text, format } => Ok(json!({"text":compact_text(&text, format)})),
        Job::RestoreText { text } => Ok(json!({"text":restore_text(&text)?.unwrap_or(text)})),
        Job::CountTokens { text } => Ok(json!({"counts":load()?.count(&text)?})),
        Job::OptimizeRequest {
            payload,
            protocol,
            selections,
            enabled,
        } => {
            let tokenizers = if enabled { Some(load()?) } else { None };
            let counters = tokenizers.as_ref().map(|value| value as &dyn TokenCounter);
            serde_json::to_value(optimize_request(
                &payload,
                protocol,
                &selections,
                counters,
                enabled,
            )?)
            .map_err(|_| Error::InvalidJson)
        }
        Job::DeriveSelections {
            payload,
            protocol,
            client,
        } => Ok(json!({"selections":derive_selections(&payload, protocol, &client)})),
        Job::CanonicalJson { value } => Ok(json!({"text":json::canonical(&value)?})),
    }
}

fn main() -> ExitCode {
    let arguments: Vec<_> = std::env::args().skip(1).collect();
    if let [command, client_flag, client, path_flag, path] = arguments.as_slice() {
        if command == "relay-bridge"
            && client_flag == "--client"
            && path_flag == "--path"
            && matches!(client.as_str(), "codex" | "claude-code")
        {
            let cache = hormuz_context_optimizer::bridge::configured_cache().unwrap_or_default();
            return match hormuz_context_optimizer::bridge::exchange(
                io::stdin(),
                io::stdout(),
                path,
                client,
                &cache,
            ) {
                Ok(()) => ExitCode::SUCCESS,
                Err(error) => {
                    eprintln!("context error: {error}");
                    ExitCode::from(2)
                }
            };
        }
    }
    if std::env::args().nth(1).as_deref() == Some("--help") {
        println!("Hormuz context optimizer qualification helper\nqualify [--tokenizer-cache DIRECTORY] < SYNTHETIC_JOB.json\nresources --check DIRECTORY\nrelay-bridge --client codex|claude-code --path PATH\nNot integrated into the shipping app; no gateway credentials or provider calls.");
        return ExitCode::SUCCESS;
    }
    match execute().and_then(|value| json::canonical(&value)) {
        Ok(output) => {
            println!("{output}");
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("context error: {error}");
            ExitCode::from(2)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn qualification_dispatch_rejects_missing_unknown_and_wrong_type_fields() {
        for input in [
            r#"{"operation":"restore_text"}"#,
            r#"{"operation":"restore_text","text":"x","unknown":0}"#,
            r#"{"operation":"compact_text","text":"x","format":"invented"}"#,
            r#"{"operation":"optimize_request","payload":{},"protocol":"chat","selections":[],"enabled":1}"#,
        ] {
            assert!(Job::parse(json::strict(input).unwrap()).is_err());
        }
    }

    #[test]
    fn qualification_dispatch_preserves_dynamic_json_objects() {
        let input =
            r#"{"operation":"canonical_json","value":{"$serde_json::private::Number":"1"}}"#;
        let Job::CanonicalJson { value } = Job::parse(json::strict(input).unwrap()).unwrap() else {
            panic!("wrong qualification operation");
        };
        assert_eq!(
            json::canonical(&value).unwrap(),
            r#"{"$serde_json::private::Number":"1"}"#
        );
    }
}
