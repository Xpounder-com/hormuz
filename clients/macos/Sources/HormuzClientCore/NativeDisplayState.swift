import Foundation

/// Validated display-only ABI projection. Credentials are never accepted as a
/// display field. The existing gateway validators still own identity/accounting.
public struct NativeDisplayState: Sendable {
    public let phase: String
    public let error: ClientError?
    public let connection: ConnectionStatus?
    public let dashboard: Dashboard?
    public let readingStatus: CompanionReadingStatus
    public var isBusy: Bool { ["checking", "signingIn", "signingOut"].contains(phase) }

    public init(data: Data) throws {
        guard data.count <= 128 * 1024 else { throw ClientError.responseTooLarge }
        let wire = try GatewayJSON.decoder().decode(Wire.self, from: data)
        guard wire.schemaVersion == 1, wire.snapshot.scope == "current_actor",
              ["checking", "ready", "signingIn", "signingOut", "failed"].contains(wire.phase) else {
            throw ClientError.invalidResponse
        }
        if let error = wire.error {
            guard let known = ClientError(rawValue: error) else { throw ClientError.invalidResponse }
            self.error = known
        } else { error = nil }
        phase = wire.phase
        if let status = wire.connection {
            let expiry = status.expiresAtEpochSeconds
            guard (status.sessionState == nil || status.profile != nil),
                  expiry == nil || (expiry!.isFinite && expiry! >= 0 && status.sessionState != nil) else {
                throw ClientError.invalidResponse
            }
            connection = ConnectionStatus(profile: status.profile, sessionState: status.sessionState,
                expiresAt: expiry.map(Date.init(timeIntervalSince1970:)))
        } else { connection = nil }
        switch wire.snapshot.reading.status {
        case "current": readingStatus = .current
        case "stale": readingStatus = .stale
        case "offline": readingStatus = .offline
        case "needsAuthentication": readingStatus = .needsAuthentication
        default: throw ClientError.invalidResponse
        }
        let reading = wire.snapshot.reading
        if let identity = wire.snapshot.identity, let usage = reading.usage,
           let checked = reading.checkedAtEpochSeconds, let profile = connection?.profile {
            guard checked.isFinite, checked >= 0, readingStatus != .needsAuthentication else { throw ClientError.invalidResponse }
            try identity.validate(for: profile)
            try usage.validate()
            dashboard = Dashboard(identity: identity, usage: usage, checkedAt: Date(timeIntervalSince1970: checked))
        } else {
            guard wire.snapshot.identity == nil, reading.usage == nil, reading.checkedAtEpochSeconds == nil,
                  readingStatus != .current else { throw ClientError.invalidResponse }
            dashboard = nil
        }
    }
    private struct Wire: Decodable {
        let schemaVersion: Int
        let phase: String
        let error: String?
        let connection: Status?
        let snapshot: Snapshot
    }
    private struct Status: Decodable {
        let profile: ConnectionProfile?
        let sessionState: SessionState?
        let expiresAtEpochSeconds: Double?
    }
    private struct Snapshot: Decodable {
        let scope: String
        let identity: GatewayIdentity?
        let reading: Reading
    }
    private struct Reading: Decodable {
        let status: String
        let usage: PersonalUsage?
        let checkedAtEpochSeconds: Double?
    }
}
