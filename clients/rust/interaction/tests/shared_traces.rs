use hormuz_client_interaction::{Event, Interaction, VisibilityMode};
use serde::Deserialize;
use serde_json::Value;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Corpus {
    schema_id: String,
    schema_version: u32,
    source_revision: String,
    source_paths: Vec<String>,
    note: String,
    cases: Vec<Trace>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Trace {
    id: String,
    note: String,
    swift_reference: bool,
    mode: VisibilityMode,
    visible: bool,
    steps: Vec<Step>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Step {
    at_ms: u64,
    events: Vec<Event>,
    expected: Value,
    effects: Value,
}

#[test]
fn shared_event_traces_match_every_state_and_effect() {
    let corpus: Corpus = serde_json::from_str(include_str!(
        "../../../../tests/fixtures/native_client/v1/interactions.json"
    ))
    .unwrap();
    assert_eq!(corpus.schema_id, "hormuz.native-client-fixtures");
    assert_eq!(corpus.schema_version, 1);
    assert_eq!(corpus.source_revision.len(), 40);
    assert_eq!(corpus.source_paths.len(), 4);
    assert!(!corpus.note.is_empty());
    assert!(corpus.cases.len() >= 12);
    assert!(
        corpus
            .cases
            .iter()
            .filter(|case| case.swift_reference)
            .count()
            >= 4
    );
    let mut ids = std::collections::HashSet::new();
    for case in corpus.cases {
        assert!(ids.insert(case.id.clone()), "duplicate trace {}", case.id);
        assert!(!case.note.is_empty(), "{}", case.id);
        let mut state = Interaction::new(case.mode, case.visible);
        for (index, step) in case.steps.into_iter().enumerate() {
            let effects = state.dispatch_batch(step.at_ms, &step.events).unwrap();
            assert_eq!(
                serde_json::to_value(state.snapshot()).unwrap(),
                step.expected,
                "{} step {}",
                case.id,
                index
            );
            assert_eq!(
                serde_json::to_value(effects).unwrap(),
                step.effects,
                "{} step {} effects",
                case.id,
                index
            );
        }
    }
}

#[test]
fn fixture_event_vocabulary_rejects_unknown_input() {
    for invalid in [
        r#"{"type":"key_down","key":"arbitrary"}"#,
        r#"{"type":"toggle_pin","metric":"unknown"}"#,
        r#"{"type":"open_settings","page":"unknown"}"#,
        r#"{"type":"toggle_pin","metric":"cost","extra":true}"#,
        r#"{"type":"timer_fired","token":-1}"#,
        r#"{"type":"timer_fired","token":1.5}"#,
        r#"{"type":"focus_changed","target":"unknown"}"#,
        r#"{"type":"pointer_enter","target":{"region":"metric","metric":"cost","extra":true}}"#,
    ] {
        assert!(serde_json::from_str::<Event>(invalid).is_err(), "{invalid}");
    }
}
