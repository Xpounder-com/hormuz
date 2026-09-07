import Foundation

public enum AIClient: String, Codable, CaseIterable, Identifiable, Sendable {
    case codex, claudeCode = "claude-code"
    public var id: String { rawValue }
    public var title: String { self == .codex ? "Codex" : "Claude Code" }
    public var executable: String { self == .codex ? "codex" : "claude" }
    public var testedVersion: String { self == .codex ? "0.147.0" : "2.1.233" }
}

public enum GatewaySetup: String, Codable, CaseIterable, Identifiable, Sendable {
    case openAIPilot = "openai-pilot"
    case custom

    public var id: String { rawValue }
}

public struct ConnectionProfile: Codable, Equatable, Sendable {
    public let id: UUID
    public let gateway: String
    public let organization: String
    public let issuer: String?
    public let client: AIClient
    public let model: String
    public let allowLoopbackHTTP: Bool
    public let setup: GatewaySetup

    public init(id: UUID = UUID(), gateway: String, organization: String, issuer: String? = nil,
                client: AIClient, model: String, allowLoopbackHTTP: Bool = false,
                setup: GatewaySetup = .custom) throws {
        let normalizedGateway = try Self.normalizeGateway(
            gateway, allowLoopbackHTTP: allowLoopbackHTTP
        )
        guard Self.safeText(organization, maximum: 200), !organization.isEmpty,
              model.range(of: #"\A[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\z"#, options: .regularExpression) != nil,
              issuer == nil || Self.safeText(issuer!, maximum: 2048) else { throw ClientError.invalidProfile }
        if setup == .openAIPilot {
            guard client == .codex,
                  ["openai-primary", "openai-secondary"].contains(model),
                  !allowLoopbackHTTP,
                  URLComponents(string: normalizedGateway)?.scheme == "https"
            else { throw ClientError.invalidProfile }
        }
        self.id = id
        self.gateway = normalizedGateway
        self.organization = organization
        self.issuer = issuer?.isEmpty == true ? nil : issuer
        self.client = client
        self.model = model
        self.allowLoopbackHTTP = allowLoopbackHTTP
        self.setup = setup
    }

    private enum CodingKeys: String, CodingKey {
        case id, gateway, organization, issuer, client, model, allowLoopbackHTTP, setup
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let setup: GatewaySetup
        if container.contains(.setup) {
            setup = try container.decode(GatewaySetup.self, forKey: .setup)
        } else {
            setup = .custom
        }
        try self.init(
            id: container.decode(UUID.self, forKey: .id),
            gateway: container.decode(String.self, forKey: .gateway),
            organization: container.decode(String.self, forKey: .organization),
            issuer: container.decodeIfPresent(String.self, forKey: .issuer),
            client: container.decode(AIClient.self, forKey: .client),
            model: container.decode(String.self, forKey: .model),
            allowLoopbackHTTP: container.decode(Bool.self, forKey: .allowLoopbackHTTP),
            setup: setup
        )
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(id, forKey: .id)
        try container.encode(gateway, forKey: .gateway)
        try container.encode(organization, forKey: .organization)
        try container.encodeIfPresent(issuer, forKey: .issuer)
        try container.encode(client, forKey: .client)
        try container.encode(model, forKey: .model)
        try container.encode(allowLoopbackHTTP, forKey: .allowLoopbackHTTP)
        try container.encode(setup, forKey: .setup)
    }

    public var key: String { id.uuidString.lowercased() }

    public func validated() throws -> ConnectionProfile {
        try ConnectionProfile(id: id, gateway: gateway, organization: organization, issuer: issuer,
                              client: client, model: model, allowLoopbackHTTP: allowLoopbackHTTP,
                              setup: setup)
    }

    public static func normalizeGateway(_ value: String, allowLoopbackHTTP: Bool) throws -> String {
        guard safeText(value, maximum: 2048), !value.contains("\\"),
              var url = URLComponents(string: value), let host = url.host, !host.isEmpty,
              url.user == nil, url.password == nil, url.query == nil, url.fragment == nil,
              url.path == "" || url.path == "/", url.port == nil || (1...65535).contains(url.port!),
              let scheme = url.scheme?.lowercased(), ["http", "https"].contains(scheme)
        else { throw ClientError.invalidGateway }
        if scheme != "https" {
            guard allowLoopbackHTTP, ["127.0.0.1", "localhost", "[::1]", "::1"].contains(host.lowercased())
            else { throw ClientError.insecureGateway }
        }
        url.scheme = scheme
        url.host = host.lowercased()
        url.path = ""
        guard let result = url.url?.absoluteString else { throw ClientError.invalidGateway }
        return result
    }

    private static func safeText(_ value: String, maximum: Int) -> Bool {
        value.utf8.count <= maximum && !value.unicodeScalars.contains { CharacterSet.controlCharacters.contains($0) }
            && value == value.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
