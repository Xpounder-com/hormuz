//! Existing credential-free, bounded one-shot optimizer protocol. The relay
//! retains local authentication, owner cancellation and provider forwarding.
use crate::{
    derive_selections, json, optimize_request, Error, Protocol, TokenCounter, Tokenizers,
    MAX_REQUEST_BYTES,
};
use std::{
    io::{Read, Write},
    path::{Path, PathBuf},
    time::{Duration, Instant},
};

pub fn transform(
    body: &[u8],
    path: &str,
    client: &str,
    gateway: &str,
    cache: &Path,
) -> Option<Vec<u8>> {
    let protocol = match (client, path) {
        ("codex", "/v1/responses" | "/v1/responses/compact") => Protocol::Responses,
        ("claude-code", "/v1/messages") => Protocol::Anthropic,
        _ => return None,
    };
    if body.len() > MAX_REQUEST_BYTES || !probe_gateway(gateway) {
        return None;
    }
    // Match Python: one-shot resource construction precedes the candidate-work
    // guard. The supervising relay bounds the complete helper lifetime.
    let tokenizers = Tokenizers::load(cache).ok()?;
    let started = Instant::now();
    let text = std::str::from_utf8(body).ok()?;
    let payload = json::strict(text).ok()?;
    if !payload.is_object() {
        return None;
    }
    let selections = derive_selections(&payload, protocol, client);
    let result = optimize_request(&payload, protocol, &selections, Some(&tokenizers), true).ok()?;
    if !result.changed {
        return None;
    }
    let outgoing = json::canonical(&result.payload).ok()?;
    // Python measures the actual boundary strings as well as candidate items.
    tokenizers.count(text).ok()?;
    tokenizers.count(&outgoing).ok()?;
    if started.elapsed() > Duration::from_millis(100)
        || outgoing.len() > MAX_REQUEST_BYTES
        || outgoing.as_bytes() == body
    {
        None
    } else {
        Some(outgoing.into_bytes())
    }
}

pub fn probe_gateway(gateway: &str) -> bool {
    let Ok(origin) = hormuz_client_core::normalize_gateway(gateway.trim_end_matches('/'), true)
    else {
        return false;
    };
    let client = match reqwest::blocking::Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(5))
        .build()
    {
        Ok(client) => client,
        Err(_) => return false,
    };
    let Ok(mut response) = client
        .get(format!("{origin}/health"))
        .header("Accept", "application/json")
        .header("Cache-Control", "no-store")
        .send()
    else {
        return false;
    };
    let headers = response.headers().get_all("x-hormuz-context-formats");
    let compatible = response.status() == reqwest::StatusCode::OK
        && headers.iter().count() == 1
        && headers
            .iter()
            .next()
            .is_some_and(|value| value == "structural-v1");
    let mut discarded = Vec::new();
    compatible
        && response
            .by_ref()
            .take(128 * 1024 + 1)
            .read_to_end(&mut discarded)
            .is_ok()
}

pub fn exchange(
    input: impl Read,
    mut output: impl Write,
    path: &str,
    client: &str,
    cache: &Path,
) -> Result<(), Error> {
    let mut input = input;
    let mut prefix = [0; 2];
    input
        .read_exact(&mut prefix)
        .map_err(|_| Error::InvalidJson)?;
    let length = u16::from_be_bytes(prefix) as usize;
    if !(1..=2048).contains(&length) {
        return Err(Error::InvalidJson);
    }
    let mut gateway = vec![0; length];
    input
        .read_exact(&mut gateway)
        .map_err(|_| Error::InvalidJson)?;
    if !gateway.is_ascii() {
        return Err(Error::InvalidJson);
    }
    let gateway = std::str::from_utf8(&gateway).map_err(|_| Error::InvalidJson)?;
    let mut body = Vec::new();
    input
        .take((MAX_REQUEST_BYTES + 1) as u64)
        .read_to_end(&mut body)
        .map_err(|_| Error::InvalidJson)?;
    if body.len() > MAX_REQUEST_BYTES {
        return Err(Error::LimitExceeded);
    }
    if let Some(changed) = transform(&body, path, client, gateway, cache) {
        output
            .write_all(&[1])
            .and_then(|_| output.write_all(&changed))
            .map_err(|_| Error::InvalidJson)?;
    } else {
        output.write_all(&[0]).map_err(|_| Error::InvalidJson)?;
    }
    Ok(())
}

pub fn configured_cache() -> Option<PathBuf> {
    if let Ok(executable) = std::env::current_exe() {
        if let Some(contents) = executable
            .parent()
            .filter(|parent| parent.file_name().is_some_and(|name| name == "Helpers"))
            .and_then(Path::parent)
            .filter(|parent| parent.file_name().is_some_and(|name| name == "Contents"))
        {
            return Some(contents.join("Resources/ContextTokenizers"));
        }
    }
    std::env::var_os("HORMUZ_CONTEXT_TOKENIZER_CACHE").map(PathBuf::from)
}
