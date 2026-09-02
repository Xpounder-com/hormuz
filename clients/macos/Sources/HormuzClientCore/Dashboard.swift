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

public struct ProviderDeploymentIdentity: Codable, Equatable, Sendable {
    public let platform: String
    public let sourceCommit: String
    public let sourceBranch: String
    public let repository: String
    public let cpuCount: String
    public let webConcurrency: String
    public let externalOrigin: String
    public let serviceId: String
    public let instanceFingerprint: String

    func validate() throws {
        guard platform == "render", sourceBranch == "main",
              repository == "Xpounder-com/hormuz", cpuCount == "0.5",
              webConcurrency == "1",
              sourceCommit.range(of: "^[0-9a-f]{40}$", options: .regularExpression) != nil,
              serviceId.range(of: "^srv-[a-z0-9]{16,32}$", options: .regularExpression) != nil,
              instanceFingerprint.range(of: "^[0-9a-f]{16}$", options: .regularExpression) != nil,
              let components = URLComponents(string: externalOrigin),
              components.scheme == "https", components.host != nil,
              components.user == nil, components.password == nil,
              components.port == nil || components.port == 443,
              components.path.isEmpty, components.query == nil,
              components.fragment == nil else {
            throw ClientError.invalidResponse
        }
    }
}

/// Minimal content-free counters and deployment identity used to prove that a
/// rejected client request never reached a model provider, its authenticated
/// retry did once, and both observations came from the approved deployment.
public struct ProviderReliabilitySnapshot: Codable, Equatable, Sendable {
    public let schemaId: String
    public let schemaVersion: Int
    public let scope: String
    public let liveProviderRequestCount: Int
    public let providerAttemptRecordCount: Int
    public let deployment: ProviderDeploymentIdentity

    func validate() throws {
        guard schemaId == "hormuz.provider-reliability-summary", schemaVersion == 1,
              scope == "current_actor", liveProviderRequestCount >= 0,
              providerAttemptRecordCount >= liveProviderRequestCount else {
            throw ClientError.invalidResponse
        }
        try deployment.validate()
    }
}

public struct ConnectionStatus: Sendable {
    public let profile: ConnectionProfile?
    public let sessionState: SessionState?
    public let expiresAt: Date?
    public var hasSession: Bool { sessionState != nil }
}
