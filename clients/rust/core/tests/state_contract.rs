use hormuz_client_core::{
    normalize_gateway, ClientError, ConnectionProfile, ConnectionStatus,
    ContextOptimizationPreference, ContextOptimizationStatus, PersonalUsage, ReadingStatus,
    SessionState, UsageReading, MAX_CONTEXT_SETTINGS_BYTES,
};
use serde_json::Value;

fn fixture(source: &str) -> Value {
    let value: Value = serde_json::from_str(source).unwrap();
    assert_eq!(value["schema_id"], "hormuz.native-client-fixtures");
    assert_eq!(value["schema_version"], 1);
    assert_eq!(value["source_revision"].as_str().unwrap().len(), 40);
    value
}

fn profiles() -> Value {
    fixture(include_str!(
        "../../../../tests/fixtures/native_client/v1/profiles.json"
    ))
}

fn profile() -> ConnectionProfile {
    ConnectionProfile::from_json(&serde_json::to_vec(&profiles()["cases"][0]["input"]).unwrap())
        .unwrap()
}

#[test]
fn shared_profiles_preserve_validation_normalization_and_legacy_defaults() {
    let fixture = profiles();
    assert_eq!(fixture["cases"].as_array().unwrap().len(), 49);
    for case in fixture["cases"].as_array().unwrap() {
        let parsed = ConnectionProfile::from_json(&serde_json::to_vec(&case["input"]).unwrap());
        assert_eq!(
            parsed.is_ok(),
            case["rust_valid"].as_bool().unwrap(),
            "{}",
            case["id"]
        );
        if let Ok(profile) = parsed {
            let output = serde_json::to_value(&profile).unwrap();
            assert_eq!(output, case["expected"], "{}", case["id"]);
            assert_eq!(
                profile.key(),
                case["input"]["id"].as_str().unwrap().to_ascii_lowercase()
            );
            let reloaded =
                ConnectionProfile::from_json(&serde_json::to_vec(&output).unwrap()).unwrap();
            assert!(profile == reloaded, "{}", case["id"]);
        }
        if case["rust_valid"] != case["swift_valid"] {
            assert!(!case["note"].as_str().unwrap().is_empty());
        }
    }
}

#[test]
fn gateway_parser_cannot_expand_the_literal_loopback_exception() {
    for host in [
        "127.1",
        "2130706433",
        "0x7f000001",
        "127.000.0.1",
        "%31%32%37.0.0.1",
        "localhost.",
        "localhost.evil.test",
        "[0:0:0:0:0:0:0:1]",
    ] {
        assert!(
            normalize_gateway(&format!("http://{host}"), true).is_err(),
            "{host}"
        );
    }
    for origin in [
        "https:///example.test",
        "https://example.test:",
        "https://[::1]extra",
        "https://example.test/..",
        "https://example.test/%2f",
        "https://example.test:999999999999",
        "https://%65xample.test",
    ] {
        assert!(normalize_gateway(origin, true).is_err(), "{origin}");
    }
}

#[test]
fn shared_context_settings_are_canonical_bounded_and_default_off() {
    let fixture = fixture(include_str!(
        "../../../../tests/fixtures/native_client/v1/context-settings.json"
    ));
    assert_eq!(fixture["cases"].as_array().unwrap().len(), 20);
    for case in fixture["cases"].as_array().unwrap() {
        let bytes = case["file_bytes"].as_str().map(str::as_bytes);
        let parsed = ContextOptimizationPreference::from_file_bytes(bytes);
        assert_eq!(
            parsed.is_ok(),
            case["expected_enabled"].is_boolean(),
            "{}",
            case["id"]
        );
        if let Ok(preference) = parsed {
            assert_eq!(
                preference.enabled(),
                case["expected_enabled"].as_bool().unwrap()
            );
            assert_eq!(
                ContextOptimizationPreference::from_file_bytes(Some(preference.to_file_bytes())),
                Ok(preference)
            );
        } else {
            assert_eq!(parsed, Err(ClientError::ContextSettingsInvalid));
        }
    }
    for bytes in [vec![0xff], vec![b' '; MAX_CONTEXT_SETTINGS_BYTES + 1]] {
        assert_eq!(
            ContextOptimizationPreference::from_file_bytes(Some(&bytes)),
            Err(ClientError::ContextSettingsInvalid)
        );
    }
}

#[test]
fn shared_state_codes_preserve_pending_sessions_and_unavailable_readings() {
    let fixture = fixture(include_str!(
        "../../../../tests/fixtures/native_client/v1/status.json"
    ));
    for case in fixture["session_states"].as_array().unwrap() {
        let state: Option<SessionState> = serde_json::from_value(case["code"].clone()).unwrap();
        let status = ConnectionStatus::new(Some(profile()), state, None).unwrap();
        assert_eq!(status.has_session(), case["has_session"].as_bool().unwrap());
        assert_eq!(
            serde_json::to_value(status.session_state()).unwrap(),
            case["code"]
        );
    }
    for code in fixture["rejected_session_states"].as_array().unwrap() {
        assert!(serde_json::from_value::<SessionState>(code.clone()).is_err());
    }
    for case in fixture["reading_states"].as_array().unwrap() {
        let state: ReadingStatus = serde_json::from_value(case["code"].clone()).unwrap();
        assert_eq!(state.is_stale(), case["is_stale"].as_bool().unwrap());
        assert_eq!(
            state.is_unavailable(),
            case["is_unavailable"].as_bool().unwrap()
        );
        assert_eq!(serde_json::to_value(state).unwrap(), case["code"]);
    }
    for case in fixture["context_states"].as_array().unwrap() {
        let state: ContextOptimizationStatus =
            serde_json::from_value(case["code"].clone()).unwrap();
        assert_eq!(state.label(), case["label"].as_str().unwrap());
        assert_eq!(serde_json::to_value(state).unwrap(), case["code"]);
    }
    assert!(serde_json::from_str::<ReadingStatus>("\"unknown\"").is_err());
    assert!(serde_json::from_str::<ContextOptimizationStatus>("\"unknown\"").is_err());
}

#[test]
fn safe_error_catalog_has_fixed_messages_and_rejects_arbitrary_input() {
    let fixture = fixture(include_str!(
        "../../../../tests/fixtures/native_client/v1/errors.json"
    ));
    assert_eq!(fixture["cases"].as_array().unwrap().len(), 22);
    for case in fixture["cases"].as_array().unwrap() {
        let error: ClientError = serde_json::from_value(case["code"].clone()).unwrap();
        assert_eq!(error.to_string(), case["native_message"].as_str().unwrap());
        assert_eq!(serde_json::to_value(error).unwrap(), case["code"]);
        if case["swift_message"] != case["native_message"] {
            assert!(!case["note"].as_str().unwrap().is_empty());
        }
    }
    for raw in [
        "\"arbitrary server response\"",
        "{\"invalidResponse\":\"server body\"}",
    ] {
        assert!(serde_json::from_str::<ClientError>(raw).is_err());
    }
}

#[test]
fn missing_readings_cannot_become_zero_or_current_and_times_remain_explicit() {
    let fixture = fixture(include_str!(
        "../../../../tests/fixtures/native_client/v1/gateway.json"
    ));
    let source = &fixture["cases"]
        .as_array()
        .unwrap()
        .iter()
        .find(|case| case["id"] == "usage_current")
        .unwrap()["response"];
    let usage = PersonalUsage::from_json(&serde_json::to_vec(source).unwrap()).unwrap();
    assert!(UsageReading::new(ReadingStatus::Current, None, None).is_err());
    assert!(UsageReading::new(ReadingStatus::Stale, Some(usage.clone()), None).is_err());
    for time in [f64::INFINITY, f64::NAN, -1.0] {
        assert!(
            UsageReading::new(ReadingStatus::Current, Some(usage.clone()), Some(time)).is_err()
        );
        assert!(
            ConnectionStatus::new(Some(profile()), Some(SessionState::Active), Some(time)).is_err()
        );
    }
    for status in [
        ReadingStatus::Stale,
        ReadingStatus::Offline,
        ReadingStatus::NeedsAuthentication,
    ] {
        let missing = UsageReading::new(status, None, None).unwrap();
        assert!(missing.usage().is_none());
        assert!(missing.checked_at_epoch_seconds().is_none());
        assert!(serde_json::to_value(missing).unwrap()["usage"].is_null());
        let retained = UsageReading::new(status, Some(usage.clone()), Some(1234.0)).unwrap();
        assert_eq!(retained.usage().unwrap().requests(), usage.requests());
        assert_eq!(retained.checked_at_epoch_seconds(), Some(1234.0));
    }
    assert!(ConnectionStatus::new(None, Some(SessionState::Active), None).is_err());
    assert!(ConnectionStatus::new(Some(profile()), None, Some(1234.0)).is_err());
}
