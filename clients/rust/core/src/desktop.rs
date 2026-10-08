use crate::{decode, AIClient, ClientError, ConnectionProfile};
use serde::Deserialize;

/// The same origin/client/version binding checked by the existing Swift shell.
/// This is not an enrollment policy override or a display-selected profile.
#[derive(Deserialize)]
pub struct DesktopProfile {
    schema_id: String,
    schema_version: i64,
    gateway_origin: String,
    organization_id: String,
    allowed_clients: Vec<AIClient>,
    client: AIClient,
    model_alias: String,
    profile_version: i64,
}
impl DesktopProfile {
    pub fn validate(bytes: &[u8], saved: &ConnectionProfile) -> Result<(), ClientError> {
        let profile: Self = decode(bytes)?;
        if profile.schema_id != "hormuz.desktop-profile"
            || profile.schema_version != 1
            || profile.profile_version <= 0
            || profile.gateway_origin != saved.gateway()
            || profile.client != saved.client()
            || profile.allowed_clients != [saved.client()]
        {
            return Err(ClientError::InvalidResponse);
        }
        if !saved.desktop_managed()
            || profile.organization_id != saved.organization()
            || profile.model_alias != saved.model()
            || Some(profile.profile_version) != saved.desktop_profile_version()
        {
            return Err(ClientError::DesktopProfileChanged);
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    fn saved() -> ConnectionProfile {
        ConnectionProfile::from_json(&serde_json::to_vec(&json!({
            "id":"12345678-1234-1234-1234-1234567890ab", "gateway":"https://gateway.example.test",
            "organization":"org-a", "client":"codex", "model":"openai-primary", "allowLoopbackHTTP":false,
            "desktopManaged":true, "desktopProfileVersion":7,
        })).unwrap()).unwrap()
    }
    fn wire() -> serde_json::Value {
        json!({"schema_id":"hormuz.desktop-profile","schema_version":1,
            "gateway_origin":"https://gateway.example.test", "organization_id":"org-a",
            "allowed_clients":["codex"], "client":"codex", "model_alias":"openai-primary", "profile_version":7})
    }
    #[test]
    fn hosted_profile_roundtrip_preserves_fields_and_rejects_invalid_bindings() {
        let saved = saved();
        let bytes = serde_json::to_vec(&saved).unwrap();
        assert!(ConnectionProfile::from_json(&bytes).unwrap() == saved);
        let value: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
        assert_eq!(value["desktopManaged"], true);
        assert_eq!(value["desktopProfileVersion"], 7);
        assert_eq!(
            DesktopProfile::validate(&serde_json::to_vec(&wire()).unwrap(), &saved),
            Ok(())
        );
        for (field, changed) in [
            ("organization_id", json!("other")),
            ("model_alias", json!("other")),
            ("profile_version", json!(8)),
            ("allowed_clients", json!(["codex", "claude-code"])),
            ("gateway_origin", json!("https://other.example.test")),
            ("schema_version", json!(2)),
        ] {
            let mut value = wire();
            value[field] = changed;
            assert!(
                DesktopProfile::validate(&serde_json::to_vec(&value).unwrap(), &saved).is_err(),
                "{field}"
            );
        }
        let mut value = serde_json::to_value(saved).unwrap();
        value["desktopManaged"] = json!(false);
        assert!(ConnectionProfile::from_json(&serde_json::to_vec(&value).unwrap()).is_err());
    }
}
