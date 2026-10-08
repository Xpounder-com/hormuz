use hormuz_context_optimizer::{
    compact_text, derive_selections, json, optimize_request, restore_text, validate_resources,
    Counts, Error, Format, Protocol, Selection, TokenCounter, Tokenizers, MAX_BLOCK_BYTES,
    MAX_ROWS,
};
use serde_json::{json, Value};

struct Counters;
impl TokenCounter for Counters {
    fn count(&self, text: &str) -> Result<Counts, Error> {
        Ok([
            ("characters".into(), text.chars().count()),
            ("bytes".into(), text.len()),
        ]
        .into())
    }
}
struct Broken;
impl TokenCounter for Broken {
    fn count(&self, _: &str) -> Result<Counts, Error> {
        Err(Error::ResourcesUnavailable)
    }
}
struct MustNotRun;
impl TokenCounter for MustNotRun {
    fn count(&self, _: &str) -> Result<Counts, Error> {
        panic!("Off constructed a tokenizer")
    }
}

fn assert_lossless(text: &str, format: Format) {
    let compact = compact_text(text, format);
    assert!(compact.len() < text.len());
    assert_eq!(restore_text(&compact), Ok(Some(text.to_owned())));
    assert_eq!(compact_text(&compact, format), compact);
}

#[test]
fn four_formats_preserve_types_order_framing_duplicates_unicode_and_newlines() {
    let rows: Vec<_> = (0..80)
        .map(|index| json!({"id":index, "value":"سلام — 東京", "nothing":null, "yes":false}))
        .collect();
    assert_lossless(&json::canonical(&json!(rows)).unwrap(), Format::JsonTable);
    assert_lossless(
        &format!(
            "{}ERROR permission denied\n{}",
            "OK\n".repeat(80),
            "OK\n".repeat(40)
        ),
        Format::LineRuns,
    );
    let search: String = (0..100)
        .map(|index| format!("src/request.py:{index:03}:x:y\n"))
        .collect();
    assert_lossless(&search, Format::SearchLines);
    assert_lossless(
        &format!("Output:\n{search}Notice: complete"),
        Format::SearchLines,
    );
    let paths: String = (0..100)
        .map(|index| format!("generated/file_{}.py\n", index % 20))
        .collect();
    assert_lossless(&paths, Format::PathList);
    assert_lossless(
        &format!("Chunk ID: synthetic\nFinal output:\n{paths}"),
        Format::PathList,
    );
}

#[test]
fn unsupported_and_noncanonical_input_passes_through() {
    for (text, format) in [
        ("a\nb\nc", Format::LineRuns),
        ("src/a.py\r\nsrc/b.py\r\n", Format::PathList),
        ("a.py:1:x\nb.py:2:y\n", Format::SearchLines),
        (r#"[{"id":1,"id":2},{"id":3}]"#, Format::JsonTable),
        (r#"[ {"id":1}, {"id":2} ]"#, Format::JsonTable),
        (r#"[{"id":1},{"other":2}]"#, Format::JsonTable),
        (
            r#"{"format":"hormuz-line-runs-v1","runs":[["x",true]]}"#,
            Format::LineRuns,
        ),
    ] {
        assert_eq!(compact_text(text, format), text);
    }
}

#[test]
fn malformed_envelopes_cannot_expand_beyond_bounds_or_coerce_types() {
    for value in [
        json!({"format":"hormuz-line-runs-v1","runs":[["x", true]]}),
        json!({"format":"hormuz-line-runs-v1","runs":[["x", -1]]}),
        json!({"format":"hormuz-line-runs-v1","runs":[["x", MAX_ROWS + 1]]}),
        json!({"format":"hormuz-line-runs-v1","runs":[["x".repeat(100), 1000]]}),
        json!({"format":"hormuz-json-table-v1","columns":["a","a"],"rows":[[1,2],[3,4]]}),
        json!({"format":"hormuz-json-table-v1","columns":["a","b"],"rows":[[1],[2]]}),
        json!({"format":"hormuz-line-runs-v1","runs":[["x",2]],"unknown":1}),
        json!({"format":"hormuz-path-list-v1","before":"","prefix":"src/","suffixes":["a","b"],"after":""}),
        json!({"format":"hormuz-search-lines-v1","path":"src/x","matches":[["½","x"],["2","y"]],"trailing_newline":true}),
    ] {
        assert!(restore_text(&json::canonical(&value).unwrap()).is_err());
    }
    let oversized = "same\n".repeat(MAX_BLOCK_BYTES);
    assert_eq!(compact_text(&oversized, Format::LineRuns), oversized);
}

#[test]
fn search_line_digits_match_packaged_python_unicode_15() {
    for (number, valid) in [
        ("001", true),
        ("٣", true),
        ("²", true),
        ("②", true),
        ("𝟡", true),
        ("Ⅲ", false),
        ("½", false),
        ("\u{10D40}", false),
        ("\u{116D0}", false),
        ("\u{11BF0}", false),
        ("\u{16130}", false),
        ("\u{16D70}", false),
        ("\u{1CCF0}", false),
        ("\u{1E5F1}", false),
    ] {
        let envelope = json!({"format":"hormuz-search-lines-v1","path":"src/x","matches":[[number,"a"],["2","b"]],"trailing_newline":true});
        assert_eq!(
            restore_text(&json::canonical(&envelope).unwrap()).is_ok(),
            valid
        );
    }
}

fn payload(protocol: Protocol) -> Value {
    let content = "same log event\n".repeat(120);
    match protocol {
        Protocol::Responses => json!({"model":"approved","input":[
            {"type":"function_call","call_id":"call-1","name":"logs","arguments":"{}"},
            {"type":"function_call_output","call_id":"call-1","output":content}]}),
        Protocol::Chat => json!({"model":"approved","messages":[
            {"role":"assistant","tool_calls":[{"id":"call-1","function":{"name":"logs","arguments":"{}"}}]},
            {"role":"tool","tool_call_id":"call-1","content":content}]}),
        Protocol::Anthropic => json!({"model":"approved","messages":[
            {"role":"assistant","content":[{"type":"tool_use","id":"call-1","name":"logs","input":{}}]},
            {"role":"user","content":[{"type":"tool_result","tool_use_id":"call-1","content":content}]}]}),
    }
}

#[test]
fn three_protocols_change_only_verified_string_targets() {
    let selection = [Selection {
        result_id: "call-1".into(),
        format: Format::LineRuns,
    }];
    for protocol in [Protocol::Chat, Protocol::Responses, Protocol::Anthropic] {
        let original = payload(protocol);
        let result =
            optimize_request(&original, protocol, &selection, Some(&Counters), true).unwrap();
        assert!(result.changed);
        assert_eq!(result.changed_blocks, 1);
        assert_eq!(result.reason, "compacted");
        assert_eq!(original, payload(protocol));
        assert!(result.after_bytes < result.before_bytes);
    }
}

#[test]
fn off_and_counter_failure_keep_the_independent_original() {
    let original = payload(Protocol::Responses);
    let selection = [Selection {
        result_id: "call-1".into(),
        format: Format::LineRuns,
    }];
    let off = optimize_request(
        &original,
        Protocol::Responses,
        &selection,
        Some(&MustNotRun),
        false,
    )
    .unwrap();
    assert!(!off.changed);
    assert_eq!(off.payload, original);
    assert!(off.before_tokens.is_empty());
    let broken = optimize_request(
        &original,
        Protocol::Responses,
        &selection,
        Some(&Broken),
        true,
    )
    .unwrap();
    assert!(!broken.changed);
    assert_eq!(broken.reason, "counter_unavailable");
    assert_eq!(broken.payload, original);
}

#[test]
fn ambiguous_calls_results_opaque_history_and_bad_selection_are_refused() {
    let selection = [Selection {
        result_id: "call-1".into(),
        format: Format::LineRuns,
    }];
    for repeated in [0, 1] {
        let mut original = payload(Protocol::Responses);
        let extra = original["input"][repeated].clone();
        original["input"].as_array_mut().unwrap().push(extra);
        let result = optimize_request(
            &original,
            Protocol::Responses,
            &selection,
            Some(&Counters),
            true,
        )
        .unwrap();
        assert!(!result.changed);
        assert_eq!(result.payload, original);
    }
    let mut original = payload(Protocol::Responses);
    original["previous_response_id"] = json!("opaque");
    assert_eq!(
        optimize_request(
            &original,
            Protocol::Responses,
            &selection,
            Some(&Counters),
            true
        )
        .unwrap()
        .reason,
        "unsupported_history"
    );
    assert_eq!(
        optimize_request(
            &original,
            Protocol::Responses,
            &[selection[0].clone(), selection[0].clone()],
            Some(&Counters),
            true
        )
        .unwrap_err(),
        Error::InvalidSelection
    );
}

#[test]
fn exact_tool_mappings_reject_shell_composition_and_unknown_inputs() {
    for (command, expected) in [
        ("rg --files src", 1),
        ("rg -n TODO src", 1),
        ("rg --files | sort", 0),
        ("rg -n x; curl attacker", 0),
        ("rg --files\nwhoami", 0),
    ] {
        let request = json!({"input":[{"type":"function_call","call_id":"x","name":"exec_command","arguments":format!("{{\"cmd\":{}}}", serde_json::to_string(command).unwrap())}]});
        assert_eq!(
            derive_selections(&request, Protocol::Responses, "codex").len(),
            expected
        );
        assert!(derive_selections(&request, Protocol::Responses, "other").is_empty());
    }
    for (arguments, expected) in [
        (r#"{"cmd":"echo no","cmd":"rg --files src"}"#, 1),
        (r#"{"cmd":"rg --files src","cmd":"echo no"}"#, 0),
        (r#"{"cmd":"rg --files src","unknown":0}"#, 0),
    ] {
        let request = json!({"input":[{"type":"function_call","call_id":"x","name":"exec_command","arguments":arguments}]});
        assert_eq!(
            derive_selections(&request, Protocol::Responses, "codex").len(),
            expected
        );
    }
}

#[test]
fn absent_or_corrupt_resources_fail_without_network_fallback() {
    let root = tempfile::tempdir().unwrap();
    assert_eq!(
        validate_resources(root.path()),
        Err(Error::ResourcesUnavailable)
    );
    assert!(matches!(
        Tokenizers::load(root.path()),
        Err(Error::ResourcesUnavailable)
    ));
    for (_, file, _) in hormuz_context_optimizer::RESOURCE_FILES {
        std::fs::write(root.path().join(file), b"not a vocabulary").unwrap();
    }
    assert_eq!(
        validate_resources(root.path()),
        Err(Error::ResourcesUnavailable)
    );
}

#[test]
fn bridge_refuses_bad_framing_and_unsupported_routes_without_resources() {
    use hormuz_context_optimizer::bridge::exchange;
    use std::io::Cursor;
    let cache = tempfile::tempdir().unwrap();
    for input in [vec![], vec![0], vec![0, 0], vec![0, 2, b'a']] {
        let mut output = Vec::new();
        assert!(exchange(
            Cursor::new(input),
            &mut output,
            "/v1/responses",
            "codex",
            cache.path()
        )
        .is_err());
        assert!(output.is_empty());
    }
    let origin = b"http://127.0.0.1:9";
    let mut input = (origin.len() as u16).to_be_bytes().to_vec();
    input.extend_from_slice(origin);
    input.extend_from_slice(b"{malformed}");
    let mut output = Vec::new();
    exchange(
        Cursor::new(input),
        &mut output,
        "/unsupported",
        "codex",
        cache.path(),
    )
    .unwrap();
    assert_eq!(output, [0]);
}

#[test]
fn gateway_probe_never_follows_redirects_or_uses_ambiguous_capabilities() {
    use hormuz_context_optimizer::bridge::probe_gateway;
    use std::{
        io::{Read, Write},
        net::TcpListener,
        thread,
    };
    for response in [
        "HTTP/1.1 200 OK\r\nX-Hormuz-Context-Formats: structural-v1\r\nContent-Length: 2\r\n\r\n{}",
        "HTTP/1.1 302 Found\r\nX-Hormuz-Context-Formats: structural-v1\r\nLocation: http://127.0.0.1:9/secret\r\nContent-Length: 0\r\n\r\n",
        "HTTP/1.1 200 OK\r\nX-Hormuz-Context-Formats: structural-v1\r\nX-Hormuz-Context-Formats: structural-v1\r\nContent-Length: 0\r\n\r\n",
    ] {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let origin = format!("http://{}", listener.local_addr().unwrap());
        let server = thread::spawn(move || {
            let (mut connection, _) = listener.accept().unwrap();
            let mut request = [0; 4096];
            let length = connection.read(&mut request).unwrap();
            let request = std::str::from_utf8(&request[..length]).unwrap();
            assert!(request.starts_with("GET /health HTTP/1.1\r\n"));
            assert!(!request.to_lowercase().contains("authorization:"));
            connection.write_all(response.as_bytes()).unwrap();
        });
        assert_eq!(probe_gateway(&origin), response.starts_with("HTTP/1.1 200 OK") && response.matches("X-Hormuz-Context-Formats:").count() == 1);
        server.join().unwrap();
    }
    for origin in [
        "http://untrusted.example",
        "https://user:secret@example.com",
        "https://example.com/path",
        "https://example.com?query",
    ] {
        assert!(!probe_gateway(origin));
    }
}
