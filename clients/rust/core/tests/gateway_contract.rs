use hormuz_client_core::{
    AIClient, ClientError, GatewayIdentity, PersonalUsage, MAX_RESPONSE_BYTES,
};
use serde::Deserialize;
use serde_json::Value;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Fixtures {
    schema_id: String,
    schema_version: u32,
    source_revision: String,
    organization: String,
    client: AIClient,
    cases: Vec<Case>,
}

#[derive(Deserialize)]
#[serde(rename_all = "lowercase")]
enum Kind {
    Usage,
    Identity,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Case {
    id: String,
    kind: Kind,
    response: Value,
    client_valid: bool,
    gateway_valid: bool,
    expected: Option<Value>,
    note: String,
}

fn fixtures() -> Fixtures {
    serde_json::from_str(include_str!(
        "../../../../tests/fixtures/native_client/v1/gateway.json"
    ))
    .expect("versioned fixture document")
}

#[test]
fn shared_gateway_vectors_match_the_existing_native_client() {
    let fixtures = fixtures();
    assert_eq!(fixtures.schema_id, "hormuz.native-client-fixtures");
    assert_eq!(fixtures.schema_version, 1);
    assert_eq!(fixtures.source_revision.len(), 40);
    assert_eq!(fixtures.cases.len(), 41);
    for case in fixtures.cases {
        if case.client_valid != case.gateway_valid {
            assert!(
                !case.note.is_empty(),
                "undocumented difference: {}",
                case.id
            );
        }
        let bytes = serde_json::to_vec(&case.response).unwrap();
        let result = match case.kind {
            Kind::Usage => {
                PersonalUsage::from_json(&bytes).map(|usage| serde_json::to_value(usage).unwrap())
            }
            Kind::Identity => {
                GatewayIdentity::from_json(&bytes, &fixtures.organization, fixtures.client)
                    .map(|identity| serde_json::to_value(identity).unwrap())
            }
        };
        assert_eq!(result.is_ok(), case.client_valid, "{}", case.id);
        if let Some(expected) = case.expected {
            let result = result.unwrap();
            for (field, value) in expected.as_object().unwrap() {
                // Integral JSON syntax may normalize from 3.0 to 3.
                if field == "cost_usd" {
                    assert_eq!(
                        result[field].as_f64(),
                        value.as_f64(),
                        "{}: {}",
                        case.id,
                        field
                    );
                } else if let Some(integer) = value.as_i64() {
                    assert_eq!(
                        result[field].as_i64(),
                        Some(integer),
                        "{}: {}",
                        case.id,
                        field
                    );
                } else if value.is_number() {
                    assert_eq!(
                        result[field].as_f64(),
                        value.as_f64(),
                        "{}: {}",
                        case.id,
                        field
                    );
                } else {
                    assert_eq!(&result[field], value, "{}: {}", case.id, field);
                }
            }
        }
    }
}

#[test]
fn identity_is_bound_to_each_supported_client_and_profile() {
    let f = fixtures();
    let bytes = serde_json::to_vec(
        &f.cases
            .iter()
            .find(|c| c.id == "identity_session")
            .unwrap()
            .response,
    )
    .unwrap();
    for client in [AIClient::Codex, AIClient::ClaudeCode] {
        assert!(GatewayIdentity::from_json(&bytes, &f.organization, client).is_ok());
        assert!(matches!(
            GatewayIdentity::from_json(&bytes, "other-org", client),
            Err(ClientError::IdentityMismatch)
        ));
    }
}

#[test]
fn missing_usage_cannot_become_a_zero_snapshot() {
    for bytes in [b"null".as_slice(), b"{}", b"[]"] {
        assert!(matches!(
            PersonalUsage::from_json(bytes),
            Err(ClientError::InvalidResponse)
        ));
    }
}

#[test]
fn parser_and_response_limit_errors_are_content_free() {
    let oversized = vec![b' '; MAX_RESPONSE_BYTES + 1];
    for error in [
        PersonalUsage::from_json(&oversized).err().unwrap(),
        PersonalUsage::from_json(b"{\"synthetic-sensitive-marker\":")
            .err()
            .unwrap(),
        GatewayIdentity::from_json(&oversized, "org-example", AIClient::Codex)
            .err()
            .unwrap(),
    ] {
        assert!(!error.to_string().contains("synthetic-sensitive-marker"));
        assert!(!format!("{error:?}").contains("synthetic-sensitive-marker"));
    }
    assert!(matches!(
        PersonalUsage::from_json(&oversized),
        Err(ClientError::ResponseTooLarge)
    ));
}

#[test]
fn unknown_fields_are_not_retained_in_the_projection() {
    let f = fixtures();
    let mut response = f
        .cases
        .iter()
        .find(|c| c.id == "usage_current")
        .unwrap()
        .response
        .clone();
    response["access_token"] = Value::String("synthetic-sensitive-marker".into());
    let usage = PersonalUsage::from_json(&serde_json::to_vec(&response).unwrap()).unwrap();
    let output = serde_json::to_string(&usage).unwrap();
    assert!(!output.contains("access_token"));
    assert!(!output.contains("synthetic-sensitive-marker"));
    assert_eq!(usage.requests(), 3);
    assert_eq!(usage.input_tokens(), 120);
    assert_eq!(usage.output_tokens(), 30);
    assert_eq!(usage.cost_usd(), 0.00012);
    assert_eq!(usage.cost_basis(), "configured_rate_card_estimate");
    assert_eq!(usage.coverage(), "gateway_captured_requests_only");
}

#[test]
fn nonfinite_cost_and_malformed_json_are_rejected() {
    let f = fixtures();
    let response = serde_json::to_string(
        &f.cases
            .iter()
            .find(|c| c.id == "usage_current")
            .unwrap()
            .response,
    )
    .unwrap();
    for invalid in ["1e999", "NaN", "Infinity", "-Infinity"] {
        let bytes = response.replace("0.00012", invalid);
        assert!(PersonalUsage::from_json(bytes.as_bytes()).is_err());
    }
    let mut exact_limit = response.into_bytes();
    exact_limit.resize(MAX_RESPONSE_BYTES, b' ');
    assert!(PersonalUsage::from_json(&exact_limit).is_ok());
    exact_limit.push(b' ');
    assert!(matches!(
        PersonalUsage::from_json(&exact_limit),
        Err(ClientError::ResponseTooLarge)
    ));
}
