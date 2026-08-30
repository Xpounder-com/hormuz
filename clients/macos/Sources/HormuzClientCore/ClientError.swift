import Foundation

/// Fixed, content-free diagnostics: never display server bodies, URLs with query
/// strings, Keychain data, tokens, or arbitrary NSError descriptions.
public enum ClientError: String, Error, LocalizedError {
    case invalidProfile, invalidGateway, insecureGateway, invalidResponse
    case gatewayUnavailable, unexpectedRedirect, responseTooLarge, loginRejected
    case loginTimedOut, loginRequired, alreadySignedIn, identityMismatch
    case refreshInterrupted, logoutPending, secureStoreUnavailable, unsafeStorage
    case storageUnavailable, profileBusy, configurationChanged, invalidArguments

    public var errorDescription: String? {
        switch self {
        case .invalidProfile: return "Enter an organization ID and approved model alias. Connection fields must not contain control characters."
        case .invalidGateway: return "Enter a gateway origin such as https://gateway.example.com, without a path or credentials."
        case .insecureGateway: return "HTTPS is required. Local development may explicitly enable HTTP for loopback only."
        case .invalidResponse: return "The gateway returned an invalid response. No credential was displayed."
        case .gatewayUnavailable: return "The gateway could not be reached. Check its address and your connection."
        case .unexpectedRedirect: return "The gateway redirected a credential request. Hormuz stopped without following it."
        case .responseTooLarge: return "The gateway response exceeded the client safety limit."
        case .loginRejected: return "The gateway rejected sign-in. Check the team, client, and identity-provider configuration."
        case .loginTimedOut: return "Sign-in timed out. Start again from Hormuz."
        case .loginRequired: return "Sign in to Hormuz to use this connection."
        case .alreadySignedIn: return "Sign out of the saved connection before changing its identity or client."
        case .identityMismatch: return "The returned identity does not match this team's client connection."
        case .refreshInterrupted: return "Credential refresh was interrupted. Sign out to revoke the session, then sign in again."
        case .logoutPending: return "Local use is disabled, but server revocation is not confirmed. Retry sign out when the gateway is available."
        case .secureStoreUnavailable: return "Keychain is unavailable or access was denied. Hormuz will not save credentials to a file."
        case .unsafeStorage: return "Hormuz storage has unsafe ownership, permissions, or a symbolic link. No file was changed."
        case .storageUnavailable: return "Hormuz could not read or save its local configuration."
        case .profileBusy: return "Another Hormuz process is updating this connection. Try again shortly."
        case .configurationChanged: return "The connector changed after preview. Review it again before saving."
        case .invalidArguments: return "Unsupported helper arguments. Open Hormuz to configure the connection."
        }
    }

    public static func message(for error: Error) -> String {
        if error is CancellationError { return "Sign-in cancelled. Any unfinished enrollment will expire." }
        return (error as? ClientError)?.errorDescription ?? "Hormuz could not complete the operation. No credentials were displayed."
    }
}
