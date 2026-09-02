import Foundation
import Security

private struct Enrollment: Decodable {
    let enrollmentId: String
    let loginUrl: String
    let expiresAt: Date
    let pollIntervalSeconds: Int
}

public actor SessionController {
    public let directory: PrivateDirectory
    private let store: any SessionStore
    private let transport: any GatewayTransport
    private let now: @Sendable () -> Date

    public init(directory: PrivateDirectory, store: any SessionStore = KeychainSessionStore(),
                transport: any GatewayTransport = HTTPGatewayTransport(), now: @escaping @Sendable () -> Date = { Date() }) {
        self.directory = directory
        self.store = store
        self.transport = transport
        self.now = now
    }

    public func status() async throws -> ConnectionStatus {
        let lock = try await directory.lock()
        defer { lock.unlock() }
        if let record = try store.load() {
            let checked = try record.validated(for: record.profile)
            // Keychain remains authoritative, including for revocation if a
            // non-secret profile was edited or removed outside the app.
            return ConnectionStatus(profile: checked.profile, sessionState: checked.state, expiresAt: checked.sessionExpiresAt)
        }
        return ConnectionStatus(profile: try directory.loadProfile(), sessionState: nil, expiresAt: nil)
    }

    public func signIn(profile: ConnectionProfile,
                       openBrowser: @Sendable (URL) async throws -> Void) async throws {
        let profile = try profile.validated()
        let lock = try await directory.lock()
        defer { lock.unlock() }
        guard try store.load() == nil else { throw ClientError.alreadySignedIn }
        try directory.saveProfile(profile)
        let secret = try Self.enrollmentSecret()
        var fields = ["client": profile.client.rawValue, "enrollment_secret": secret, "organization_id": profile.organization]
        if let issuer = profile.issuer { fields["issuer"] = issuer }
        let reply = try await post(profile, "/v1/auth/enrollments", fields)
        guard reply.status == 201 else { throw ClientError.loginRejected }
        let enrollment = try reply.decode(Enrollment.self)
        guard enrollment.enrollmentId.range(of: #"\A[A-Za-z0-9_-]{32}\z"#, options: .regularExpression) != nil,
              (1...10).contains(enrollment.pollIntervalSeconds), enrollment.expiresAt > now(),
              let loginURL = URL(string: enrollment.loginUrl),
              loginURL.absoluteString == profile.gateway + "/v1/auth/login?enrollment=" + enrollment.enrollmentId
        else { throw ClientError.invalidResponse }
        try Task.checkCancellation()
        try await openBrowser(loginURL)
        guard let pollingMilliseconds = Self.enrollmentPollingMilliseconds(
            expiresAt: enrollment.expiresAt, now: now()
        ) else { throw ClientError.loginTimedOut }
        let deadline = ContinuousClock.now + .milliseconds(pollingMilliseconds)
        while ContinuousClock.now < deadline {
            try Task.checkCancellation()
            let result = try await post(profile, "/v1/auth/enrollments/" + enrollment.enrollmentId + "/redeem",
                                        ["enrollment_secret": secret])
            if result.status == 200 {
                let record = try result.decode(CredentialPair.self).record(profile: profile, now: now())
                // Validate server-resolved identity before retaining a session.
                var saved = false
                do {
                    try Task.checkCancellation()
                    _ = try await identity(profile, token: record.accessToken)
                    try Task.checkCancellation()
                    try store.save(record)
                    saved = true
                    try Task.checkCancellation()
                } catch {
                    if saved {
                        var suspended = record
                        suspended.state = .revocationPending
                        try? store.save(suspended)
                    }
                    let revoked = await revokeUnsaved(record)
                    if saved && revoked { try? store.delete() }
                    throw error
                }
                return
            }
            guard result.status == 409 else { throw ClientError.loginRejected }
            try await Task.sleep(for: .seconds(enrollment.pollIntervalSeconds))
        }
        throw ClientError.loginTimedOut
    }

    static func enrollmentPollingMilliseconds(expiresAt: Date, now: Date) -> Int? {
        let milliseconds = expiresAt.timeIntervalSince(now) * 1_000
        guard milliseconds > 0, milliseconds <= Double(Int.max) else { return nil }
        return max(1, Int(milliseconds.rounded(.up)))
    }

    /// The only intentional secret output is the credential command's stdout.
    /// Calls are serialized across the app and all helper processes.
    public func accessCredential(profileID: UUID, forceRefresh: Bool = false) async throws -> String {
        let lock = try await directory.lock()
        defer { lock.unlock() }
        return try await credentialWhileLocked(profileID: profileID, forceRefresh: forceRefresh).accessToken
    }

    public func dashboard(profileID: UUID) async throws -> Dashboard {
        let lock = try await directory.lock()
        defer { lock.unlock() }
        let record = try await credentialWhileLocked(profileID: profileID)
        async let who = identity(record.profile, token: record.accessToken)
        async let use = usage(record.profile, token: record.accessToken)
        return try await Dashboard(identity: who, usage: use, checkedAt: now())
    }

    /// Content-free operational checks used by the signed pilot workflow. They
    /// deliberately return no credential or server body to their caller.
    public func verifySession(profileID: UUID) async throws {
        let lock = try await directory.lock()
        defer { lock.unlock() }
        let record = try await credentialWhileLocked(profileID: profileID)
        _ = try await identity(record.profile, token: record.accessToken)
    }

    public func reliability(profileID: UUID) async throws -> ProviderReliabilitySnapshot {
        let lock = try await directory.lock()
        defer { lock.unlock() }
        let record = try await credentialWhileLocked(profileID: profileID)
        let reply = try await transport.request(
            profile: record.profile,
            path: "/v1/gateway/reliability",
            body: nil,
            accessToken: record.accessToken
        )
        guard reply.status != 401 else { throw ClientError.loginRequired }
        guard reply.status == 200 else { throw ClientError.gatewayUnavailable }
        let value = try reply.decode(ProviderReliabilitySnapshot.self)
        try value.validate()
        return value
    }

    public func refreshSession(profileID: UUID) async throws {
        let lock = try await directory.lock()
        defer { lock.unlock() }
        _ = try await credentialWhileLocked(profileID: profileID, forceRefresh: true)
    }

    /// Revoke the current server session while retaining the Keychain record so
    /// a subsequent check can prove that the gateway rejects that exact session.
    public func revokeServerSession(profileID: UUID) async throws {
        let lock = try await directory.lock()
        defer { lock.unlock() }
        guard let profile = try directory.loadProfile(), profile.id == profileID,
              let saved = try store.load() else { throw ClientError.loginRequired }
        let record = try saved.validated(for: profile)
        guard record.state == .active else { throw ClientError.refreshInterrupted }
        let reply = try await post(profile, "/v1/auth/logout", ["credential": record.refreshToken])
        struct Revoked: Decodable { let revoked: Bool }
        guard reply.status == 200, try reply.decode(Revoked.self).revoked else {
            throw ClientError.gatewayUnavailable
        }
    }

    public func verifyServerRevocation(profileID: UUID) async throws {
        let lock = try await directory.lock()
        defer { lock.unlock() }
        guard let profile = try directory.loadProfile(), profile.id == profileID,
              let saved = try store.load() else { throw ClientError.loginRequired }
        let record = try saved.validated(for: profile)
        guard record.state == .active else { throw ClientError.refreshInterrupted }
        let reply = try await transport.request(
            profile: profile,
            path: "/v1/gateway/whoami",
            body: nil,
            accessToken: record.accessToken
        )
        guard reply.status == 401 else { throw ClientError.invalidResponse }
    }

    public func verifySessionAbsent(profileID: UUID) async throws {
        let lock = try await directory.lock()
        defer { lock.unlock() }
        guard let profile = try directory.loadProfile(), profile.id == profileID else {
            throw ClientError.loginRequired
        }
        guard try store.load() == nil else { throw ClientError.alreadySignedIn }
    }

    /// Prove that the shared Keychain slot is empty before a controlled-pilot
    /// login. This deliberately does not trust a profile file: a stale or
    /// removed profile must not hide a retained session record.
    public func verifySessionStoreEmpty() async throws {
        let lock = try await directory.lock()
        defer { lock.unlock() }
        guard try store.load() == nil else { throw ClientError.alreadySignedIn }
    }

    public func signOut() async throws {
        let lock = try await directory.lock()
        defer { lock.unlock() }
        guard let saved = try store.load() else { return }
        var record = try saved.validated(for: saved.profile)
        // Suspend local use BEFORE contacting the gateway. Preserve this token
        // solely for an idempotent revocation retry, even after a process crash.
        record.state = .revocationPending
        try store.save(record)
        do {
            let reply = try await post(record.profile, "/v1/auth/logout", ["credential": record.refreshToken])
            struct Revoked: Decodable { let revoked: Bool }
            guard reply.status == 200, try reply.decode(Revoked.self).revoked else { throw ClientError.logoutPending }
        } catch { throw ClientError.logoutPending }
        try store.delete()
    }

    private func credentialWhileLocked(profileID: UUID, forceRefresh: Bool = false) async throws -> SessionRecord {
        guard let profile = try directory.loadProfile(), profile.id == profileID,
              let saved = try store.load() else { throw ClientError.loginRequired }
        var record = try saved.validated(for: profile)
        if record.state == .revocationPending { throw ClientError.logoutPending }
        guard record.state == .active else { throw ClientError.refreshInterrupted }
        guard record.sessionExpiresAt > now() else { throw ClientError.loginRequired }
        if !forceRefresh, record.accessExpiresAt.timeIntervalSince(now()) > 60 { return record }
        // Write a refresh intent first. A crash or ambiguous response must not
        // cause another helper to replay a possibly-consumed refresh token.
        record.state = .refreshPending
        try store.save(record)
        let response: GatewayReply
        do { response = try await post(profile, "/v1/auth/refresh", ["refresh_token": record.refreshToken]) }
        catch { throw ClientError.refreshInterrupted }
        guard response.status == 200 else { throw ClientError.refreshInterrupted }
        let updated = try response.decode(CredentialPair.self).record(profile: profile, now: now())
        guard updated.sessionExpiresAt == record.sessionExpiresAt,
              updated.refreshToken != record.refreshToken, updated.accessToken != record.accessToken else {
            await revokeUnsaved(updated)
            throw ClientError.invalidResponse
        }
        do { try store.save(updated) }
        catch { await revokeUnsaved(updated); throw error }
        return updated
    }

    private func identity(_ profile: ConnectionProfile, token: String) async throws -> GatewayIdentity {
        let reply = try await transport.request(profile: profile, path: "/v1/gateway/whoami", body: nil, accessToken: token)
        guard reply.status != 401 else { throw ClientError.loginRequired }
        guard reply.status == 200 else { throw ClientError.gatewayUnavailable }
        let value = try reply.decode(GatewayIdentity.self)
        try value.validate(for: profile)
        return value
    }

    private func usage(_ profile: ConnectionProfile, token: String) async throws -> PersonalUsage {
        let reply = try await transport.request(profile: profile, path: "/v1/gateway/usage", body: nil, accessToken: token)
        guard reply.status != 401 else { throw ClientError.loginRequired }
        guard reply.status == 200 else { throw ClientError.gatewayUnavailable }
        let value = try reply.decode(PersonalUsage.self)
        try value.validate()
        return value
    }

    private func post(_ profile: ConnectionProfile, _ path: String, _ value: [String: String]) async throws -> GatewayReply {
        try await transport.request(profile: profile, path: path,
                                    body: JSONSerialization.data(withJSONObject: value), accessToken: nil)
    }

    @discardableResult private func revokeUnsaved(_ record: SessionRecord) async -> Bool {
        // Cleanup gets its own uncancelled task: cancellation after redemption
        // must not silently skip server revocation. Never retry refresh here.
        let transport = self.transport
        return await Task.detached {
            do {
                let reply = try await transport.request(profile: record.profile, path: "/v1/auth/logout",
                    body: JSONSerialization.data(withJSONObject: ["credential": record.refreshToken]), accessToken: nil)
                struct Revocation: Decodable { let revoked: Bool }
                return reply.status == 200 && (try? reply.decode(Revocation.self).revoked) == true
            } catch { return false }
        }.value
    }

    private static func enrollmentSecret() throws -> String {
        var bytes = [UInt8](repeating: 0, count: 48)
        guard SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) == errSecSuccess else {
            throw ClientError.secureStoreUnavailable
        }
        return Data(bytes).base64EncodedString().replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
    }
}
