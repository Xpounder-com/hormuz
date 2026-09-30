import Foundation

public struct DesktopProfile: Decodable, Sendable {
    public let schemaId: String
    public let schemaVersion: Int
    public let gatewayOrigin: String
    public let organizationId: String
    public let allowedClients: [AIClient]
    public let client: AIClient
    public let modelAlias: String
    public let profileVersion: Int

    public func connection(origin: String, client expectedClient: AIClient,
                           allowLoopbackHTTP: Bool) throws -> ConnectionProfile {
        let expectedOrigin = try ConnectionProfile.normalizeGateway(origin, allowLoopbackHTTP: allowLoopbackHTTP)
        guard schemaId == "hormuz.desktop-profile", schemaVersion == 1, profileVersion > 0,
              gatewayOrigin == expectedOrigin, client == expectedClient,
              allowedClients == [expectedClient] else { throw ClientError.invalidResponse }
        return try ConnectionProfile(gateway: gatewayOrigin, organization: organizationId,
            client: client, model: modelAlias, allowLoopbackHTTP: allowLoopbackHTTP,
            desktopManaged: true)
    }

    public func validate(_ saved: ConnectionProfile) throws {
        let refreshed = try connection(origin: saved.gateway, client: saved.client,
                                       allowLoopbackHTTP: saved.allowLoopbackHTTP)
        guard saved.desktopManaged == true, refreshed.gateway == saved.gateway,
              refreshed.organization == saved.organization, refreshed.model == saved.model else {
            throw ClientError.desktopProfileChanged
        }
    }
}
