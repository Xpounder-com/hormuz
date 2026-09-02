import Foundation

public struct GatewayIdentity: Decodable, Sendable {
    public let schemaId: String
    public let schemaVersion: Int
    public let actorId: String
    public let actorName: String
    public let teamId: String
    public let teamName: String
    public let organizationId: String
    public let identityType: String
    public let allowedClients: [String]
    public let authenticationSource: String

    func validate(for profile: ConnectionProfile) throws {
        guard schemaId == "hormuz.gateway-identity", schemaVersion == 1,
              organizationId == profile.organization, allowedClients.contains(profile.client.rawValue),
              identityType == "human", authenticationSource.hasPrefix("session:"),
              !actorId.isEmpty, !actorName.isEmpty, actorName.utf8.count <= 1024,
              teamName.utf8.count <= 1024 else { throw ClientError.identityMismatch }
    }
}

public struct PersonalUsage: Decodable, Sendable {
    public let schemaId: String
    public let schemaVersion: Int
    public let month: String
    public let requests: Int
    public let deniedRequests: Int
    public let rateLimitedRequests: Int
    public let inputTokens: Int
    public let outputTokens: Int
    public let costUsd: Double
    public let costBasis: String
    public let coverage: String
    public let redactions: Int

    func validate() throws {
        guard schemaId == "hormuz.gateway-usage-summary", schemaVersion == 1, month == "current",
              [requests, deniedRequests, rateLimitedRequests, inputTokens, outputTokens, redactions].allSatisfy({ $0 >= 0 }),
              costUsd.isFinite, costUsd >= 0,
              costBasis == "configured_rate_card_estimate", coverage == "gateway_captured_requests_only"
        else { throw ClientError.invalidResponse }
    }
}

public struct Dashboard: Sendable {
    public let identity: GatewayIdentity
    public let usage: PersonalUsage
    public let checkedAt: Date
}

public struct ConnectionStatus: Sendable {
    public let profile: ConnectionProfile?
    public let sessionState: SessionState?
    public let expiresAt: Date?
    public var hasSession: Bool { sessionState != nil }
}
