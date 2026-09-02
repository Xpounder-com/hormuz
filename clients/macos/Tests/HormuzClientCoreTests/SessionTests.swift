import XCTest
@testable import HormuzClientCore

final class SessionTests: PrivateStorageTestCase {
    func testEnrollmentDeadlineHonorsGatewayExpirationBeyondFiveMinutes() {
        let now = Date(timeIntervalSince1970: 1_780_000_000)
        XCTAssertEqual(
            SessionController.enrollmentPollingMilliseconds(
                expiresAt: now.addingTimeInterval(600), now: now
            ),
            600_000
        )
        XCTAssertNil(
            SessionController.enrollmentPollingMilliseconds(expiresAt: now, now: now)
        )
    }

    func testCancellationAfterSecureStoreCommitStillRevokesAndRemovesSession() async throws {
        let clock = TestClock(), store = MemorySessions()
        let transport = FixtureTransport(clock: clock)
        let controller = SessionController(directory: directory, store: store, transport: transport, now: { clock.now() })
        let profile = try profile()
        store.cancelOnNextSave = true
        let login = Task { try await controller.signIn(profile: profile) { _ in } }
        do { try await login.value; XCTFail("Expected cancelled login") }
        catch { XCTAssertTrue(error is CancellationError) }
        XCTAssertNil(try store.load())
        let counts = await transport.counts()
        XCTAssertEqual(counts.1, 1)
    }

    func testLoginIdentityUsageAndRevocationLeaveNoCredentialFiles() async throws {
        let clock = TestClock(), store = MemorySessions(), transport = FixtureTransport(clock: TestClock())
        let controller = SessionController(directory: directory, store: store, transport: transport, now: { clock.now() })
        let profile = try profile()
        try await controller.signIn(profile: profile) { url in
            XCTAssertEqual(url.host, "gateway.example.test")
            XCTAssertEqual(url.path, "/v1/auth/login")
        }
        let dashboard = try await controller.dashboard(profileID: profile.id)
        XCTAssertEqual(dashboard.identity.organizationId, "org-a")
        XCTAssertEqual(dashboard.usage.requests, 4)
        for file in try FileManager.default.contentsOfDirectory(at: directory.root, includingPropertiesForKeys: nil) {
            let data = try Data(contentsOf: file)
            XCTAssertFalse(String(decoding: data, as: UTF8.self).contains("hox_"))
        }
        try await controller.signOut()
        XCTAssertNil(try store.load())
        let counts = await transport.counts()
        XCTAssertEqual(counts.1, 1)
    }

    func testConcurrentHelpersRotateOnceAndShareSavedResult() async throws {
        let clock = TestClock(), store = MemorySessions()
        let transport = FixtureTransport(clock: clock)
        let first = SessionController(directory: directory, store: store, transport: transport, now: { clock.now() })
        let second = SessionController(directory: directory, store: store, transport: transport, now: { clock.now() })
        let profile = try profile()
        try await first.signIn(profile: profile) { _ in }
        clock.advance(550)
        async let one = first.accessCredential(profileID: profile.id)
        async let two = second.accessCredential(profileID: profile.id)
        let results = try await [one, two]
        XCTAssertTrue(results[0] == results[1])
        let counts = await transport.counts()
        XCTAssertEqual(counts.0, 1)
    }

    func testLostRefreshResponseCannotReplayAfterControllerRestart() async throws {
        let clock = TestClock(), store = MemorySessions()
        let transport = FixtureTransport(clock: clock)
        let controller = SessionController(directory: directory, store: store, transport: transport, now: { clock.now() })
        let profile = try profile()
        try await controller.signIn(profile: profile) { _ in }
        clock.advance(550)
        await transport.setRefreshFailure()
        do { _ = try await controller.accessCredential(profileID: profile.id); XCTFail("Expected interrupted refresh") }
        catch { XCTAssertEqual(error as? ClientError, .refreshInterrupted) }
        let restarted = SessionController(directory: directory, store: store, transport: transport, now: { clock.now() })
        do { _ = try await restarted.accessCredential(profileID: profile.id); XCTFail("Expected fail closed") }
        catch { XCTAssertEqual(error as? ClientError, .refreshInterrupted) }
        let counts = await transport.counts()
        XCTAssertEqual(counts.0, 1)
        try await restarted.signOut()
        XCTAssertNil(try store.load())
    }

    func testFailedLogoutDisablesHelperButRetainsRevocationRetry() async throws {
        let clock = TestClock(), store = MemorySessions()
        let transport = FixtureTransport(clock: clock)
        let controller = SessionController(directory: directory, store: store, transport: transport, now: { clock.now() })
        let profile = try profile()
        try await controller.signIn(profile: profile) { _ in }
        await transport.setLogoutFailure(true)
        do { try await controller.signOut(); XCTFail("Expected revocation failure") }
        catch { XCTAssertEqual(error as? ClientError, .logoutPending) }
        do { _ = try await controller.accessCredential(profileID: profile.id); XCTFail("Expected disabled helper") }
        catch { XCTAssertEqual(error as? ClientError, .logoutPending) }
        XCTAssertEqual(try store.load()?.state, .revocationPending)
        await transport.setLogoutFailure(false)
        try await controller.signOut()
        XCTAssertNil(try store.load())
    }

    func testContentFreePilotOperationsRefreshRevokeProveDenialAndRemoveSession() async throws {
        let clock = TestClock(), store = MemorySessions(), transport = FixtureTransport(clock: TestClock())
        let controller = SessionController(directory: directory, store: store, transport: transport, now: { clock.now() })
        let profile = try profile()
        try await controller.verifySessionStoreEmpty()
        try await controller.signIn(profile: profile) { _ in }

        do {
            try await controller.verifySessionStoreEmpty()
            XCTFail("Expected a retained session to fail the clean-login preflight")
        } catch {
            XCTAssertEqual(error as? ClientError, .alreadySignedIn)
        }

        try await controller.verifySession(profileID: profile.id)
        let reliability = try await controller.reliability(profileID: profile.id)
        XCTAssertEqual(
            reliability,
            ProviderReliabilitySnapshot(
                schemaId: "hormuz.provider-reliability-summary",
                schemaVersion: 1,
                scope: "current_actor",
                liveProviderRequestCount: 4,
                providerAttemptRecordCount: 5,
                deployment: ProviderDeploymentIdentity(
                    platform: "render",
                    sourceCommit: String(repeating: "a", count: 40),
                    sourceBranch: "main",
                    repository: "Xpounder-com/hormuz",
                    cpuCount: "0.5",
                    webConcurrency: "1",
                    externalOrigin: "https://hormuz-pilot.onrender.com",
                    serviceId: "srv-aaaaaaaaaaaaaaaaaaaa",
                    instanceFingerprint: String(repeating: "b", count: 16)
                )
            )
        )
        try await controller.refreshSession(profileID: profile.id)
        var counts = await transport.counts()
        XCTAssertEqual(counts.0, 1)

        try await controller.revokeServerSession(profileID: profile.id)
        XCTAssertNotNil(try store.load())
        try await controller.verifyServerRevocation(profileID: profile.id)

        // Logout is deliberately idempotent at the gateway and removes the
        // locally retained record only after the second revocation succeeds.
        try await controller.signOut()
        try await controller.verifySessionAbsent(profileID: profile.id)
        try await controller.verifySessionStoreEmpty()
        XCTAssertNil(try store.load())
        counts = await transport.counts()
        XCTAssertEqual(counts.1, 2)
    }

    func testPilotOperationsRemainBoundToTheSavedProfile() async throws {
        let clock = TestClock(), store = MemorySessions(), transport = FixtureTransport(clock: clock)
        let controller = SessionController(directory: directory, store: store, transport: transport, now: { clock.now() })
        let profile = try profile()
        try await controller.signIn(profile: profile) { _ in }
        let other = UUID()

        for operation in [
            { try await controller.verifySession(profileID: other) },
            { try await controller.refreshSession(profileID: other) },
            { try await controller.revokeServerSession(profileID: other) },
            { try await controller.verifyServerRevocation(profileID: other) },
        ] {
            do { try await operation(); XCTFail("Expected profile binding failure") }
            catch { XCTAssertEqual(error as? ClientError, .loginRequired) }
        }
    }

    func testPilotReliabilityRejectsImpossibleCounterRelationship() async throws {
        let clock = TestClock(), store = MemorySessions(), transport = FixtureTransport(clock: clock)
        let controller = SessionController(directory: directory, store: store, transport: transport, now: { clock.now() })
        let profile = try profile()
        try await controller.signIn(profile: profile) { _ in }
        await transport.setBadReliability()

        do {
            _ = try await controller.reliability(profileID: profile.id)
            XCTFail("Expected invalid reliability evidence")
        } catch {
            XCTAssertEqual(error as? ClientError, .invalidResponse)
        }
    }

    func testPilotReliabilityRejectsInvalidDeploymentIdentity() async throws {
        let clock = TestClock(), store = MemorySessions(), transport = FixtureTransport(clock: clock)
        let controller = SessionController(directory: directory, store: store, transport: transport, now: { clock.now() })
        let profile = try profile()
        try await controller.signIn(profile: profile) { _ in }
        await transport.setBadDeploymentIdentity()

        do {
            _ = try await controller.reliability(profileID: profile.id)
            XCTFail("Expected invalid deployment evidence")
        } catch {
            XCTAssertEqual(error as? ClientError, .invalidResponse)
        }
    }

    func testCrossOrganizationIdentityAndUnsafeBrowserURLAreRejected() async throws {
        let clock = TestClock(), store = MemorySessions()
        let transport = FixtureTransport(clock: clock)
        let controller = SessionController(directory: directory, store: store, transport: transport, now: { clock.now() })
        await transport.setWrongOrganization()
        do { try await controller.signIn(profile: profile()) { _ in }; XCTFail("Expected identity rejection") }
        catch { XCTAssertEqual(error as? ClientError, .identityMismatch) }
        XCTAssertNil(try store.load())
        var counts = await transport.counts()
        XCTAssertEqual(counts.1, 1)
        await transport.setBadLoginURL()
        do { try await controller.signIn(profile: profile()) { _ in XCTFail("Must not open a foreign origin") }; XCTFail("Expected URL rejection") }
        catch { XCTAssertEqual(error as? ClientError, .invalidResponse) }
        counts = await transport.counts()
        XCTAssertEqual(counts.1, 1)
    }

    func testKeychainSaveFailureRevokesJustCreatedSession() async throws {
        let clock = TestClock(), store = MemorySessions()
        let transport = FixtureTransport(clock: clock)
        let controller = SessionController(directory: directory, store: store, transport: transport, now: { clock.now() })
        store.failNextSave = true
        do { try await controller.signIn(profile: profile()) { _ in }; XCTFail("Expected storage failure") }
        catch { XCTAssertEqual(error as? ClientError, .secureStoreUnavailable) }
        XCTAssertNil(try store.load())
        let counts = await transport.counts()
        XCTAssertEqual(counts.1, 1)
    }

    func testChangingProfileCannotRedirectCredentialAndLogoutUsesTrustedOrigin() async throws {
        let clock = TestClock(), store = MemorySessions()
        let transport = FixtureTransport(clock: clock)
        let controller = SessionController(directory: directory, store: store, transport: transport, now: { clock.now() })
        let profile = try profile()
        try await controller.signIn(profile: profile) { _ in }
        let changed = try ConnectionProfile(id: profile.id, gateway: "https://attacker.test", organization: "org-a", client: .claudeCode, model: "other")
        try directory.saveProfile(changed)
        do { _ = try await controller.accessCredential(profileID: profile.id); XCTFail("Expected binding rejection") }
        catch { XCTAssertEqual(error as? ClientError, .identityMismatch) }
        try await controller.signOut()
        XCTAssertNil(try store.load())
    }
}
