use serde::{Deserialize, Serialize};
use std::fmt;

/// Fixed diagnostics only: variants never carry server text, URLs or credentials.
#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub enum ClientError {
    InvalidProfile,
    InvalidGateway,
    InsecureGateway,
    InvalidResponse,
    GatewayUnavailable,
    UnexpectedRedirect,
    ResponseTooLarge,
    LoginRejected,
    LoginTimedOut,
    LoginRequired,
    AlreadySignedIn,
    IdentityMismatch,
    RefreshInterrupted,
    LogoutPending,
    SecureStoreUnavailable,
    UnsafeStorage,
    StorageUnavailable,
    ProfileBusy,
    ConfigurationChanged,
    InvalidArguments,
    ContextSettingsInvalid,
    ContextHelperUnavailable,
}

impl fmt::Display for ClientError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::InvalidProfile => "Enter an organization ID and approved model alias. Connection fields must not contain control characters.",
            Self::InvalidGateway => "Enter a gateway origin such as https://gateway.example.com, without a path or credentials.",
            Self::InsecureGateway => "HTTPS is required. Local development may explicitly enable HTTP for loopback only.",
            Self::InvalidResponse => "The gateway returned an invalid response. No credential was displayed.",
            Self::GatewayUnavailable => "The gateway could not be reached. Check its address and your connection.",
            Self::UnexpectedRedirect => "The gateway redirected a credential request. Hormuz stopped without following it.",
            Self::ResponseTooLarge => "The gateway response exceeded the client safety limit.",
            Self::LoginRejected => "The gateway rejected sign-in. Check the team, client, and identity-provider configuration.",
            Self::LoginTimedOut => "Sign-in timed out. Start again from Hormuz.",
            Self::LoginRequired => "Sign in to Hormuz to use this connection.",
            Self::AlreadySignedIn => "Sign out of the saved connection before changing its identity or client.",
            Self::IdentityMismatch => "The returned identity does not match this team's client connection.",
            Self::RefreshInterrupted => "Credential refresh was interrupted. Sign out to revoke the session, then sign in again.",
            Self::LogoutPending => "Local use is disabled, but server revocation is not confirmed. Retry sign out when the gateway is available.",
            Self::SecureStoreUnavailable => "The operating system credential store is unavailable or access was denied. Hormuz will not save credentials to a file.",
            Self::UnsafeStorage => "Hormuz storage has unsafe ownership, permissions, or a symbolic link. No file was changed.",
            Self::StorageUnavailable => "Hormuz could not read or save its local configuration.",
            Self::ProfileBusy => "Another Hormuz process is updating this connection. Try again shortly.",
            Self::ConfigurationChanged => "The connector changed after preview. Review it again before saving.",
            Self::InvalidArguments => "Unsupported helper arguments. Open Hormuz to configure the connection.",
            Self::ContextSettingsInvalid => "The local context optimization setting could not be verified. Optimization remains off.",
            Self::ContextHelperUnavailable => "The local context optimization helper is unavailable. Save a new connector after reinstalling Hormuz.",
        })
    }
}

impl std::error::Error for ClientError {}
