use crate::connection::{Phase, View};
use hormuz_client_core::{ClientError, ConnectionProfile, ReadingStatus};

/// Inputs are non-secret. Enrollment and all credentials belong to the worker.
pub fn profile(
    gateway: &str,
    organization: &str,
    model: &str,
    issuer: &str,
    client: &str,
) -> Result<ConnectionProfile, ClientError> {
    let mut bytes = [0u8; 16];
    getrandom::fill(&mut bytes).map_err(|_| ClientError::StorageUnavailable)?;
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    let id = format!(
        "{:08x}-{:04x}-{:04x}-{:04x}-{:012x}",
        u32::from_be_bytes(bytes[0..4].try_into().unwrap()),
        u16::from_be_bytes(bytes[4..6].try_into().unwrap()),
        u16::from_be_bytes(bytes[6..8].try_into().unwrap()),
        u16::from_be_bytes(bytes[8..10].try_into().unwrap()),
        u64::from_be_bytes([
            0, 0, bytes[10], bytes[11], bytes[12], bytes[13], bytes[14], bytes[15]
        ])
    );
    ConnectionProfile::from_json(
        &serde_json::to_vec(&serde_json::json!({
            "id":id, "gateway":gateway, "organization":organization, "model":model,
            "issuer":if issuer.is_empty() {None} else {Some(issuer)}, "client":client,
            "allowLoopbackHTTP":false, "setup":"custom"
        }))
        .map_err(|_| ClientError::InvalidProfile)?,
    )
}

pub fn labels(view: &View) -> [String; 5] {
    let reading = view.snapshot.reading();
    let freshness = match reading.status() {
        ReadingStatus::Current => "Current",
        ReadingStatus::Stale => "Stale",
        ReadingStatus::Offline => "Offline",
        ReadingStatus::NeedsAuthentication => "Sign-in required",
    };
    let state = match view.phase {
        Phase::Checking => "Checking saved connection...".into(),
        Phase::SigningIn => "Complete sign-in in your browser. Sign out cancels.".into(),
        Phase::SigningOut => "Local usage cleared. Confirming server sign-out...".into(),
        Phase::Failed(error) => error.to_string(),
        Phase::Ready if !view.has_session() => {
            "Enter your gateway and organization, then sign in.".into()
        }
        Phase::Ready => format!("{freshness} · Your gateway usage"),
    };
    // Always show the original successful sample time, including retained
    // offline values; never display a successful zero for a missing reading.
    let time = reading
        .checked_at_epoch_seconds()
        .map(time_label)
        .unwrap_or_else(|| "No successful reading".into());
    let heading = format!("{freshness} · {time}");
    match reading.usage() {
        Some(usage) => [
            heading,
            state,
            format!("Your requests: {}", usage.requests()),
            format!("Your tokens: {}", view.snapshot.total_tokens().unwrap()),
            format!(
                "Est. cost: ${:.4} · gateway requests only",
                usage.cost_usd()
            ),
        ],
        None => [
            heading,
            state,
            "Your requests: —".into(),
            "Your tokens: —".into(),
            "Your estimated cost: —".into(),
        ],
    }
}
fn time_label(seconds: f64) -> String {
    time::OffsetDateTime::from_unix_timestamp(seconds as i64)
        .ok()
        .and_then(|time| {
            time.format(&time::format_description::well_known::Rfc3339)
                .ok()
        })
        .map(|time| format!("Last success: {time}"))
        .unwrap_or_else(|| "Last success time unavailable".into())
}
