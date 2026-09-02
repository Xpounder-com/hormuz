import Foundation
import XCTest
@testable import HormuzClientCore

final class MemorySessions: SessionStore, @unchecked Sendable {
    private let mutex = NSLock()
    private var value: SessionRecord?
    var failNextSave = false
    var cancelOnNextSave = false
    func load() throws -> SessionRecord? { mutex.lock(); defer { mutex.unlock() }; return value }
    func save(_ record: SessionRecord) throws {
        mutex.lock(); defer { mutex.unlock() }
        if failNextSave { failNextSave = false; throw ClientError.secureStoreUnavailable }
        value = record
        if cancelOnNextSave { cancelOnNextSave = false; withUnsafeCurrentTask { $0?.cancel() } }
    }
    func delete() throws { mutex.lock(); defer { mutex.unlock() }; value = nil }
}

final class TestClock: @unchecked Sendable {
    private let mutex = NSLock()
    private var value = Date(timeIntervalSince1970: 1_780_000_000)
    func now() -> Date { mutex.lock(); defer { mutex.unlock() }; return value }
    func advance(_ seconds: TimeInterval) { mutex.lock(); defer { mutex.unlock() }; value.addTimeInterval(seconds) }
}

actor FixtureTransport: GatewayTransport {
    let clock: TestClock
    let absoluteExpiry: Date
    var refreshCount = 0
    var logoutCount = 0
    var refreshFails = false
    var logoutFails = false
    var wrongOrganization = false
    var badLoginURL = false
    var badReliability = false
    var badDeploymentIdentity = false
    var generation = 0
    var sessionRevoked = false

    init(clock: TestClock) { self.clock = clock; absoluteExpiry = clock.now().addingTimeInterval(43_200) }
    func setRefreshFailure() { refreshFails = true }
    func setLogoutFailure(_ flag: Bool) { logoutFails = flag }
    func setWrongOrganization() { wrongOrganization = true }
    func setBadLoginURL() { badLoginURL = true }
    func setBadReliability() { badReliability = true }
    func setBadDeploymentIdentity() { badDeploymentIdentity = true }
    func counts() -> (Int, Int) { (refreshCount, logoutCount) }

    func request(profile: ConnectionProfile, path: String, body: Data?, accessToken: String?) async throws -> GatewayReply {
        switch path {
        case "/v1/auth/enrollments":
            let enrollment = String(repeating: "e", count: 32)
            return try reply(201, ["enrollment_id": enrollment,
                "login_url": (badLoginURL ? "https://attacker.test" : profile.gateway) + "/v1/auth/login?enrollment=" + enrollment,
                "expires_at": iso(clock.now().addingTimeInterval(300)), "poll_interval_seconds": 1])
        case let value where value.hasSuffix("/redeem"):
            return try pair()
        case "/v1/auth/refresh":
            refreshCount += 1
            if refreshFails { throw ClientError.gatewayUnavailable }
            generation += 1
            return try pair()
        case "/v1/auth/logout":
            logoutCount += 1
            if logoutFails { throw ClientError.gatewayUnavailable }
            sessionRevoked = true
            return try reply(200, ["revoked": true])
        case "/v1/gateway/whoami":
            if sessionRevoked { return try reply(401, ["error": "unauthorized"]) }
            return try reply(200, ["schema_id": "hormuz.gateway-identity", "schema_version": 1,
                "actor_id": "alice", "actor_name": "Alice", "team_id": "engineering", "team_name": "Engineering",
                "organization_id": wrongOrganization ? "other-org" : profile.organization,
                "identity_type": "human", "allowed_clients": [profile.client.rawValue], "authentication_source": "session:fixture"])
        case "/v1/gateway/usage":
            return try reply(200, ["schema_id": "hormuz.gateway-usage-summary", "schema_version": 1, "month": "current",
                "requests": 4, "denied_requests": 1, "rate_limited_requests": 0, "input_tokens": 11, "output_tokens": 7,
                "cost_usd": 0.012, "cost_basis": "configured_rate_card_estimate", "coverage": "gateway_captured_requests_only", "redactions": 2])
        case "/v1/gateway/reliability":
            if sessionRevoked { return try reply(401, ["error": "unauthorized"]) }
            return try reply(200, [
                "schema_id": "hormuz.provider-reliability-summary", "schema_version": 1,
                "scope": "current_actor", "live_provider_request_count": 4,
                "provider_attempt_record_count": badReliability ? 3 : 5,
                "deployment": [
                    "platform": "render",
                    "source_commit": String(
                        repeating: badDeploymentIdentity ? "z" : "a", count: 40
                    ),
                    "source_branch": "main", "repository": "Xpounder-com/hormuz",
                    "cpu_count": "0.5", "web_concurrency": "1",
                    "external_origin": "https://hormuz-pilot.onrender.com",
                    "service_id": "srv-aaaaaaaaaaaaaaaaaaaa",
                    "instance_fingerprint": String(repeating: "b", count: 16),
                ],
            ])
        default: throw ClientError.invalidArguments
        }
    }

    private func pair() throws -> GatewayReply {
        let letter = String(UnicodeScalar(97 + generation)!)
        return try reply(200, ["access_token": "hox_a_" + String(repeating: letter, count: 43),
            "refresh_token": "hox_r_" + String(repeating: letter, count: 43), "token_type": "Bearer",
            "access_expires_at": iso(clock.now().addingTimeInterval(600)), "session_expires_at": iso(absoluteExpiry)])
    }
    private func reply(_ status: Int, _ value: [String: Any]) throws -> GatewayReply {
        GatewayReply(status: status, data: try JSONSerialization.data(withJSONObject: value))
    }
    private func iso(_ value: Date) -> String { ISO8601DateFormatter().string(from: value) }
}

class PrivateStorageTestCase: XCTestCase {
    var temporary: URL!
    var directory: PrivateDirectory!
    override func setUpWithError() throws {
        temporary = FileManager.default.temporaryDirectory.appendingPathComponent("hormuz-mac-tests-" + UUID().uuidString)
        directory = try PrivateDirectory(root: temporary)
    }
    override func tearDownWithError() throws { try FileManager.default.removeItem(at: temporary) }
    func profile(client: AIClient = .codex) throws -> ConnectionProfile {
        try ConnectionProfile(gateway: "https://gateway.example.test", organization: "org-a", client: client, model: "approved-alias")
    }
}
