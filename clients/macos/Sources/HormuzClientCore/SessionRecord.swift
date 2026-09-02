import Foundation

public enum SessionState: String, Codable, Sendable { case active, refreshPending, revocationPending }

/// This object is encoded ONLY into Keychain. Its description never reveals data.
public struct SessionRecord: Codable, CustomStringConvertible, CustomDebugStringConvertible, Sendable {
    public let profile: ConnectionProfile
    public let accessToken: String
    public let refreshToken: String
    public let accessExpiresAt: Date
    public let sessionExpiresAt: Date
    public var state: SessionState
    public var description: String { "SessionRecord(<redacted>)" }
    public var debugDescription: String { description }

    public init(profile: ConnectionProfile, accessToken: String, refreshToken: String,
                accessExpiresAt: Date, sessionExpiresAt: Date, state: SessionState = .active) throws {
        guard Self.validToken(accessToken, prefix: "hox_a_"), Self.validToken(refreshToken, prefix: "hox_r_"),
              accessExpiresAt.timeIntervalSince1970.isFinite, sessionExpiresAt.timeIntervalSince1970.isFinite,
              accessExpiresAt <= sessionExpiresAt else { throw ClientError.invalidResponse }
        self.profile = try profile.validated()
        self.accessToken = accessToken
        self.refreshToken = refreshToken
        self.accessExpiresAt = accessExpiresAt
        self.sessionExpiresAt = sessionExpiresAt
        self.state = state
    }

    public func validated(for expected: ConnectionProfile) throws -> SessionRecord {
        guard profile == expected else { throw ClientError.identityMismatch }
        return try SessionRecord(profile: profile, accessToken: accessToken, refreshToken: refreshToken,
                                 accessExpiresAt: accessExpiresAt, sessionExpiresAt: sessionExpiresAt, state: state)
    }

    public static func validToken(_ value: String, prefix: String) -> Bool {
        value.hasPrefix(prefix) && value.utf8.count == prefix.utf8.count + 43
            && value.dropFirst(prefix.count).allSatisfy { $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "_" || $0 == "-") }
    }
}

struct CredentialPair: Decodable {
    let accessToken: String
    let refreshToken: String
    let tokenType: String
    let accessExpiresAt: Date
    let sessionExpiresAt: Date

    func record(profile: ConnectionProfile, now: Date) throws -> SessionRecord {
        guard tokenType == "Bearer", accessExpiresAt > now, sessionExpiresAt > now,
              accessExpiresAt.timeIntervalSince(now) <= 960,
              sessionExpiresAt.timeIntervalSince(now) <= 43_260 else { throw ClientError.invalidResponse }
        return try SessionRecord(profile: profile, accessToken: accessToken, refreshToken: refreshToken,
                                 accessExpiresAt: accessExpiresAt, sessionExpiresAt: sessionExpiresAt)
    }
}

public enum GatewayJSON {
    public static func decoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        decoder.dateDecodingStrategy = .custom { value in
            let raw = try value.singleValueContainer().decode(String.self)
            let fractional = ISO8601DateFormatter()
            fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            guard let date = fractional.date(from: raw) ?? ISO8601DateFormatter().date(from: raw) else {
                throw ClientError.invalidResponse
            }
            return date
        }
        return decoder
    }
}
