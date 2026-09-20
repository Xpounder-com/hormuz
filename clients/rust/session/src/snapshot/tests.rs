use super::*;
use serde_json::{json, Value};

fn fixture() -> Value {
    serde_json::from_str(include_str!(
        "../../../../../tests/fixtures/native_client/v1/snapshots.json"
    ))
    .unwrap()
}
fn profile() -> ConnectionProfile {
    ConnectionProfile::from_json(&serde_json::to_vec(&fixture()["profile"]).unwrap()).unwrap()
}
fn identity() -> GatewayIdentity {
    GatewayIdentity::from_json(
        &serde_json::to_vec(&fixture()["identity"]).unwrap(),
        "org-a",
        hormuz_client_core::AIClient::Codex,
    )
    .unwrap()
}
fn usage(zero: bool) -> PersonalUsage {
    PersonalUsage::from_json(
        &serde_json::to_vec(&fixture()[if zero { "zero" } else { "usage" }]).unwrap(),
    )
    .unwrap()
}

#[test]
fn shared_freshness_vectors_preserve_last_success_and_emit_only_changes() {
    for case in fixture()["cases"].as_array().unwrap() {
        let snapshots = Snapshots::default();
        assert!(snapshots.take_change().is_some());
        let ticket = snapshots.begin(&profile());
        for (index, event) in case["events"].as_array().unwrap().iter().enumerate() {
            let time = 1780000000.0 + index as f64;
            match event.as_str().unwrap() {
                "success" | "zero" => {
                    snapshots.verify_identity(&ticket, &identity()).unwrap();
                    snapshots
                        .success(
                            &ticket,
                            identity(),
                            usage(event == "zero"),
                            time,
                            Duration::ZERO,
                        )
                        .unwrap();
                }
                "offline" => snapshots.failure(&ticket, ClientError::GatewayUnavailable),
                "malformed" => snapshots.failure(&ticket, ClientError::InvalidResponse),
                "unauthorized" => snapshots.failure(&ticket, ClientError::LoginRequired),
                _ => unreachable!(),
            }
            let snapshot = snapshots.snapshot();
            assert_eq!(
                serde_json::to_value(snapshot.reading().status()).unwrap(),
                case["states"][index]
            );
            assert_eq!(
                snapshot.reading().usage().is_some(),
                case["has_usage"][index].as_bool().unwrap()
            );
            assert_eq!(
                snapshots.take_change().is_some(),
                case["changes"][index].as_bool().unwrap()
            );
            assert!(snapshots.take_change().is_none());
            if snapshot.reading().usage().is_some() {
                assert_eq!(
                    snapshot.reading().checked_at_epoch_seconds(),
                    Some(if event == "success" || event == "zero" {
                        time
                    } else {
                        1780000000.0
                    })
                );
                assert_eq!(
                    snapshot.reading().usage().unwrap().cost_basis(),
                    "configured_rate_card_estimate"
                );
                assert_eq!(
                    snapshot.reading().usage().unwrap().coverage(),
                    "gateway_captured_requests_only"
                );
                assert_eq!(snapshot.scope(), "current_actor");
            }
        }
    }
}

#[test]
fn late_success_and_failure_cannot_cross_attempt_signout_profile_or_cache_boundaries() {
    let snapshots = Snapshots::default();
    let old = snapshots.begin(&profile());
    let current = snapshots.begin(&profile());
    snapshots
        .success(&current, identity(), usage(true), 2.0, Duration::ZERO)
        .unwrap();
    snapshots
        .success(&old, identity(), usage(false), 1.0, Duration::ZERO)
        .unwrap();
    snapshots.failure(&old, ClientError::LoginRequired);
    assert_eq!(snapshots.snapshot().total_tokens(), Some(0));
    snapshots.invalidate(ReadingStatus::NeedsAuthentication);
    snapshots
        .success(&current, identity(), usage(false), 3.0, Duration::ZERO)
        .unwrap();
    assert!(snapshots.snapshot().identity().is_none());
    let current = snapshots.begin(&profile());
    snapshots
        .success(&current, identity(), usage(false), 4.0, Duration::ZERO)
        .unwrap();
    let mut other = fixture()["profile"].clone();
    other["id"] = json!("00000000-0000-0000-0000-000000000336");
    snapshots.begin(&ConnectionProfile::from_json(&serde_json::to_vec(&other).unwrap()).unwrap());
    snapshots
        .success(&current, identity(), usage(false), 5.0, Duration::ZERO)
        .unwrap();
    assert!(snapshots.snapshot().reading().usage().is_none());
    let foreign = Snapshots::default();
    let ticket = foreign.begin(&profile());
    snapshots
        .success(&ticket, identity(), usage(false), 6.0, Duration::ZERO)
        .unwrap();
    assert!(snapshots.snapshot().reading().usage().is_none());
}

#[test]
fn changed_credential_or_identity_cannot_reuse_the_previous_persons_snapshot() {
    let snapshots = Snapshots::default();
    let ticket = snapshots.begin(&profile());
    snapshots.bind_credential(&profile(), "synthetic-old");
    snapshots.verify_identity(&ticket, &identity()).unwrap();
    snapshots
        .success(&ticket, identity(), usage(false), 1.0, Duration::ZERO)
        .unwrap();
    let mut input = fixture()["identity"].clone();
    input["actor_id"] = json!("bob");
    let bob = GatewayIdentity::from_json(
        &serde_json::to_vec(&input).unwrap(),
        "org-a",
        hormuz_client_core::AIClient::Codex,
    )
    .unwrap();
    assert_eq!(
        snapshots.verify_identity(&ticket, &bob),
        Err(ClientError::IdentityMismatch)
    );
    assert!(snapshots.snapshot().reading().usage().is_none());
    // A repeat request with the same credential cannot erase the rejection.
    snapshots.bind_credential(&profile(), "synthetic-old");
    assert_eq!(
        snapshots.verify_identity(&ticket, &bob),
        Err(ClientError::IdentityMismatch)
    );
    snapshots.bind_credential(&profile(), "synthetic-new");
    snapshots.failure(&ticket, ClientError::GatewayUnavailable);
    assert!(snapshots.snapshot().identity().is_none());
    snapshots.verify_identity(&ticket, &bob).unwrap();
}

#[test]
fn token_totals_are_exact_above_signed_integer_limit_and_snapshots_exclude_secrets() {
    let mut raw = fixture()["usage"].clone();
    raw["input_tokens"] = json!(i64::MAX);
    raw["output_tokens"] = json!(i64::MAX);
    // Unknown credential members are stripped by the contract projection.
    raw["access_token"] = json!("synthetic-private-marker");
    let value = PersonalUsage::from_json(&serde_json::to_vec(&raw).unwrap()).unwrap();
    let snapshots = Snapshots::default();
    let ticket = snapshots.begin(&profile());
    snapshots
        .success(&ticket, identity(), value, 1.0, Duration::ZERO)
        .unwrap();
    assert_eq!(snapshots.snapshot().total_tokens(), Some(u64::MAX - 1));
    let output = serde_json::to_string(snapshots.snapshot().as_ref()).unwrap();
    for forbidden in [
        "synthetic-private-marker",
        "access_token",
        "refresh_token",
        "credential",
    ] {
        assert!(!output.contains(forbidden));
    }
    let old = snapshots.snapshot();
    snapshots.invalidate(ReadingStatus::NeedsAuthentication);
    assert_eq!(old.total_tokens(), Some(u64::MAX - 1));
    assert!(snapshots.snapshot().total_tokens().is_none());
}

#[test]
fn contradictory_scope_cost_basis_and_coverage_cannot_be_relabelled_as_personal() {
    for (field, value) in [
        ("scope", json!("organization")),
        ("scope", Value::Null),
        ("cost_basis", json!("final_invoice")),
        ("coverage", json!("all_organization_activity")),
        ("allocation_basis", json!("allocated_organization_cost")),
        ("allocation_basis", Value::Null),
        ("month", json!("all_time")),
    ] {
        let mut raw = fixture()["usage"].clone();
        raw[field] = value;
        assert!(personal_usage(&serde_json::to_vec(&raw).unwrap()).is_err());
    }
    let mut raw = fixture()["usage"].clone();
    raw.as_object_mut().unwrap().remove("allocation_basis");
    assert!(personal_usage(&serde_json::to_vec(&raw).unwrap()).is_err());
    let mut raw = fixture()["usage"].clone();
    raw["scope"] = json!("current_actor");
    assert!(personal_usage(&serde_json::to_vec(&raw).unwrap()).is_ok());
}
