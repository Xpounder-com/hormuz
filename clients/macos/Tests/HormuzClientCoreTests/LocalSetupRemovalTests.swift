import Darwin
import Foundation
import XCTest
@testable import HormuzClientCore

final class LocalSetupRemovalTests: PrivateStorageTestCase {
    private func fixture() throws -> (SessionController, MemorySessions, FixtureTransport, ConnectionProfile) {
        let profile = try profile()
        try directory.saveProfile(profile)
        let store = MemorySessions(), clock = TestClock(), transport = FixtureTransport(clock: TestClock())
        try store.save(SessionRecord(profile: profile,
            accessToken: "hox_a_" + String(repeating: "a", count: 43),
            refreshToken: "hox_r_" + String(repeating: "r", count: 43),
            accessExpiresAt: clock.now().addingTimeInterval(600),
            sessionExpiresAt: clock.now().addingTimeInterval(43_200)))
        return (SessionController(directory: directory, store: store, transport: transport, now: clock.now),
                store, transport, profile)
    }

    private func launcher(_ profile: ConnectionProfile) async throws -> ConnectorPlan {
        let plan = try ConnectorPlan.preview(profile: profile, directory: directory,
            helper: URL(fileURLWithPath: "/fixture/Hormuz.app/Contents/MacOS/Hormuz"),
            ownerSocket: URL(fileURLWithPath: "/private/tmp/hormuz-owner-11111111-1111-1111-1111-111111111111/lease"))
        try await plan.apply(in: directory)
        return plan
    }

    func testAbsentRootIsNoOpWithoutCreatingState() async throws {
        let missing = temporary.appendingPathComponent("never-created")
        let absent = try PrivateDirectory(root: missing, create: false)
        let controller = SessionController(directory: absent, store: MemorySessions(), transport: FixtureTransport(clock: TestClock()))
        let preview = try await controller.previewLocalSetupRemoval()
        XCTAssertFalse(preview.has_session)
        XCTAssertEqual(preview.generated_files, 0)
        let result = try await controller.applyLocalSetupRemoval(previewToken: preview.preview_token)
        XCTAssertEqual(result.removed_files, 0)
        XCTAssertFalse(absent.exists)
    }

    func testOnlyENOENTProvesAbsenceAndPresenceErrorsFailClosed() throws {
        XCTAssertFalse(try PrivateDirectory.presence(result: -1, error: ENOENT))
        XCTAssertTrue(try PrivateDirectory.presence(result: 0, error: ENOENT))
        for error in [EACCES, EIO, ENOTDIR, ELOOP] {
            XCTAssertThrowsError(try PrivateDirectory.presence(result: -1, error: error)) {
                XCTAssertEqual($0 as? ClientError, .storageUnavailable)
            }
        }
    }

    func testAbsentRootReadsSharedStoreOnlyWhileNamespaceGateIsHeld() async throws {
        let missing = temporary.appendingPathComponent("never-created")
        let absent = try PrivateDirectory(root: missing, create: false)
        let gateDirectory = try PrivateDirectory(root: temporary.appendingPathComponent("shared-gate"))
        let gate = NativeSessionCoordination(directory: gateDirectory)
        let store = GateCheckingSessions(path: try gateDirectory.fileURL("native-session.lock").path)
        let controller = SessionController(directory: absent, store: store,
            transport: FixtureTransport(clock: TestClock()), coordination: gate)
        let preview = try await controller.previewLocalSetupRemoval()
        let result = try await controller.applyLocalSetupRemoval(previewToken: preview.preview_token)
        let verification = try await controller.verifyLocalSetupRemoval()
        XCTAssertTrue(result.keychain_session_absent)
        XCTAssertTrue(verification.generated_setup_absent)
        XCTAssertEqual(store.guardedReads, 3)
        XCTAssertFalse(absent.exists)
    }

    func testAbsentRootVerificationWaitsForConcurrentSharedSessionRemoval() async throws {
        let (_, store, _, _) = try fixture()
        let paused = PausedRemovalLogout(base: FixtureTransport(clock: TestClock()))
        let gateDirectory = try PrivateDirectory(root: temporary.appendingPathComponent("shared-gate"))
        let gate = NativeSessionCoordination(directory: gateDirectory)
        let first = SessionController(directory: directory, store: store, transport: paused, coordination: gate)
        let absent = try PrivateDirectory(root: temporary.appendingPathComponent("absent"), create: false)
        let second = SessionController(directory: absent, store: store,
            transport: FixtureTransport(clock: TestClock()), coordination: gate)
        let preview = try await first.previewLocalSetupRemoval()
        let removal = Task { try await first.applyLocalSetupRemoval(previewToken: preview.preview_token) }
        var entered = false
        for _ in 0..<50 {
            if await paused.isEntered() { entered = true; break }
            try await Task.sleep(for: .milliseconds(20))
        }
        XCTAssertTrue(entered)
        let verification = Task { try await second.verifyLocalSetupRemoval() }
        try await Task.sleep(for: .milliseconds(50))
        await paused.release()
        _ = try await removal.value
        let result = try await verification.value
        XCTAssertTrue(result.keychain_session_absent)
        XCTAssertFalse(absent.exists)
    }

    func testVerificationRejectsExistingSetupAndPreservesExhaustedRetryJournal() async throws {
        let (controller, store, _, _) = try fixture()
        do { _ = try await controller.verifyLocalSetupRemoval(); XCTFail("Expected incomplete setup") }
        catch { XCTAssertEqual(error as? ClientError, .removalPending) }
        try store.delete()
        let intent = NativeRemovalIntent(schemaVersion: 1, sessionFingerprint: nil, profileID: nil,
            sessionWasPresent: false, revoked: false, files: [], removedCount: 0)
        try LocalSetupRemoval.save(intent, directory: directory)
        XCTAssertThrowsError(try LocalSetupRemoval.verify(directory: directory, record: nil, allowPending: true))
        XCTAssertNotNil(try directory.read(NativeOwnership.intentName))
    }

    func testPreviewDoesNotRevokeOrChangeSetup() async throws {
        let (controller, store, transport, _) = try fixture()
        let before = try directory.read("profile.json")
        _ = try await controller.previewLocalSetupRemoval()
        XCTAssertEqual(try directory.read("profile.json"), before)
        XCTAssertEqual(try store.load()?.state, .active)
        XCTAssertNil(try directory.read(NativeOwnership.intentName))
        let counts = await transport.counts()
        XCTAssertEqual(counts.1, 0)
    }

    func testOwnedSetupRemovedRevokedAndRepeatIsIdempotent() async throws {
        let (controller, store, transport, profile) = try fixture()
        let plan = try await launcher(profile)
        _ = try await ContextOptimizationSettings.save(enabled: true, profile: profile, directory: directory)
        let preview = try await controller.previewLocalSetupRemoval()
        let result = try await controller.applyLocalSetupRemoval(previewToken: preview.preview_token)
        XCTAssertTrue(result.server_revocation_confirmed)
        XCTAssertTrue(result.session_was_present)
        XCTAssertTrue(result.keychain_session_absent)
        XCTAssertTrue(result.generated_setup_absent)
        XCTAssertNil(try store.load())
        XCTAssertNil(try directory.read("profile.json"))
        XCTAssertNil(try directory.read(plan.launcher.lastPathComponent))
        XCTAssertNil(try directory.read(ContextOptimizationSettings.fileName(profile: profile)))
        XCTAssertNotNil(try directory.read("connection.lock"))
        let again = try await controller.previewLocalSetupRemoval()
        let repeated = try await controller.applyLocalSetupRemoval(previewToken: again.preview_token)
        XCTAssertEqual(repeated.removed_files, 0)
        let counts = await transport.counts()
        XCTAssertEqual(counts.1, 1)
        let verified = try await controller.verifyLocalSetupRemoval()
        XCTAssertFalse(verified.server_revocation_confirmed)
    }

    func testChangedSharedSessionAbortsBeforeRevocation() async throws {
        let (controller, store, transport, _) = try fixture()
        let preview = try await controller.previewLocalSetupRemoval()
        let other = try profile(client: .claudeCode)
        let clock = TestClock()
        try store.save(SessionRecord(profile: other, accessToken: "hox_a_" + String(repeating: "b", count: 43),
            refreshToken: "hox_r_" + String(repeating: "b", count: 43),
            accessExpiresAt: clock.now().addingTimeInterval(600), sessionExpiresAt: clock.now().addingTimeInterval(1000)))
        do { _ = try await controller.applyLocalSetupRemoval(previewToken: preview.preview_token); XCTFail("Expected mismatch") }
        catch { XCTAssertEqual(error as? ClientError, .identityMismatch) }
        XCTAssertEqual(try store.load()?.profile, other)
        XCTAssertNil(try directory.read(NativeOwnership.intentName))
        let counts = await transport.counts(); XCTAssertEqual(counts.1, 0)
    }

    func testEditedAndUnownedFilesRemainVisibleAndUnchanged() async throws {
        let (controller, _, _, profile) = try fixture()
        let plan = try await launcher(profile)
        let edited = Data("edited by customer\n".utf8)
        try directory.write(edited, to: plan.launcher.lastPathComponent, expected: directory.read(plan.launcher.lastPathComponent))
        let sentinel = Data("synthetic unrelated bytes".utf8)
        try directory.write(sentinel, to: "unrelated.json", expected: nil)
        try directory.write(sentinel, to: "backup-11111111-1111-1111-1111-111111111111.txt", expected: nil)
        let preview = try await controller.previewLocalSetupRemoval()
        let result = try await controller.applyLocalSetupRemoval(previewToken: preview.preview_token)
        XCTAssertEqual(result.status, "complete_with_retained_files")
        XCTAssertEqual(result.retained_files, 3)
        XCTAssertEqual(try directory.read(plan.launcher.lastPathComponent), edited)
        XCTAssertEqual(try directory.read("unrelated.json"), sentinel)
    }

    func testSharedPersonalOptimizerPreferenceIsPreserved() async throws {
        let (controller, _, _, profile) = try fixture()
        _ = try await ContextOptimizationSettings.save(enabled: true, profile: profile, directory: directory)
        let preference = try directory.read(ContextOptimizationSettings.fileName(profile: profile))
        try FileManager.default.createDirectory(at: temporary.appendingPathComponent("personal-profiles"),
                                               withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        let preview = try await controller.previewLocalSetupRemoval()
        let result = try await controller.applyLocalSetupRemoval(previewToken: preview.preview_token)
        XCTAssertEqual(try directory.read(ContextOptimizationSettings.fileName(profile: profile)), preference)
        XCTAssertEqual(result.retained_files, 2)
    }

    func testDanglingPersonalProfilesEntryRetainsSharedPreferenceWithoutTargetTraversal() async throws {
        let (controller, _, _, profile) = try fixture()
        _ = try await ContextOptimizationSettings.save(enabled: true, profile: profile, directory: directory)
        let name = ContextOptimizationSettings.fileName(profile: profile)
        let preference = try XCTUnwrap(directory.read(name))
        let entry = temporary.appendingPathComponent("personal-profiles")
        let missingTarget = temporary.appendingPathComponent("missing-personal-target")
        try FileManager.default.createSymbolicLink(at: entry, withDestinationURL: missingTarget)
        let destination = try FileManager.default.destinationOfSymbolicLink(atPath: entry.path)
        let preview = try await controller.previewLocalSetupRemoval()
        let result = try await controller.applyLocalSetupRemoval(previewToken: preview.preview_token)
        XCTAssertEqual(result.status, "complete_with_retained_files")
        XCTAssertEqual(result.retained_files, 2)
        XCTAssertEqual(try directory.read(name), preference)
        XCTAssertEqual(try FileManager.default.destinationOfSymbolicLink(atPath: entry.path), destination)
        var targetInfo = stat()
        let missingResult = lstat(missingTarget.path, &targetInfo)
        let missingError = errno
        XCTAssertEqual(missingResult, -1)
        XCTAssertEqual(missingError, ENOENT)
        let verified = try await controller.verifyLocalSetupRemoval()
        XCTAssertTrue(verified.generated_setup_absent)
        XCTAssertEqual(verified.retained_files, 2)
    }

    func testNonDirectoryPersonalProfilesEntryRetainsSharedPreferenceUnchanged() async throws {
        let (controller, _, _, profile) = try fixture()
        _ = try await ContextOptimizationSettings.save(enabled: true, profile: profile, directory: directory)
        let name = ContextOptimizationSettings.fileName(profile: profile)
        let preference = try XCTUnwrap(directory.read(name))
        let entryBytes = Data("synthetic unrelated non-directory entry".utf8)
        try directory.write(entryBytes, to: "personal-profiles", expected: nil)
        let preview = try await controller.previewLocalSetupRemoval()
        let result = try await controller.applyLocalSetupRemoval(previewToken: preview.preview_token)
        XCTAssertEqual(result.status, "complete_with_retained_files")
        XCTAssertEqual(result.retained_files, 2)
        XCTAssertEqual(try directory.read(name), preference)
        XCTAssertEqual(try directory.read("personal-profiles"), entryBytes)
        let verified = try await controller.verifyLocalSetupRemoval()
        XCTAssertTrue(verified.generated_setup_absent)
        XCTAssertEqual(verified.retained_files, 2)
    }

    func testOfflineRevocationRetainsIntentAndDisablesCredentialsUntilRetry() async throws {
        let (controller, store, transport, profile) = try fixture()
        await transport.setLogoutFailure(true)
        let preview = try await controller.previewLocalSetupRemoval()
        do { _ = try await controller.applyLocalSetupRemoval(previewToken: preview.preview_token); XCTFail("Expected offline") }
        catch { XCTAssertEqual(error as? ClientError, .logoutPending) }
        XCTAssertEqual(try store.load()?.state, .revocationPending)
        XCTAssertNotNil(try directory.read(NativeOwnership.intentName))
        do { _ = try await controller.accessCredential(profileID: profile.id); XCTFail("Expected closed custody") }
        catch { XCTAssertEqual(error as? ClientError, .removalPending) }
        await transport.setLogoutFailure(false)
        let retry = try await controller.previewLocalSetupRemoval()
        XCTAssertTrue(retry.pending)
        _ = try await controller.applyLocalSetupRemoval(previewToken: retry.preview_token)
        XCTAssertNil(try store.load())
    }

    func testKeychainDeleteFailureResumesWithoutRepeatingConfirmedLogout() async throws {
        let (controller, store, transport, _) = try fixture()
        store.failNextDelete = true
        let preview = try await controller.previewLocalSetupRemoval()
        do { _ = try await controller.applyLocalSetupRemoval(previewToken: preview.preview_token); XCTFail("Expected custody failure") }
        catch { XCTAssertEqual(error as? ClientError, .secureStoreUnavailable) }
        let restarted = SessionController(directory: directory, store: store, transport: transport)
        let retry = try await restarted.previewLocalSetupRemoval()
        let result = try await restarted.applyLocalSetupRemoval(previewToken: retry.preview_token)
        XCTAssertTrue(result.server_revocation_confirmed)
        let counts = await transport.counts(); XCTAssertEqual(counts.1, 1)
    }

    func testOrdinarySignOutCannotOrphanAnOfflineRemovalIntent() async throws {
        let (controller, store, transport, _) = try fixture()
        await transport.setLogoutFailure(true)
        let preview = try await controller.previewLocalSetupRemoval()
        do { _ = try await controller.applyLocalSetupRemoval(previewToken: preview.preview_token); XCTFail("Expected offline") }
        catch { XCTAssertEqual(error as? ClientError, .logoutPending) }
        await transport.setLogoutFailure(false)
        do { try await controller.signOut(); XCTFail("Expected removal-only recovery") }
        catch { XCTAssertEqual(error as? ClientError, .removalPending) }
        XCTAssertNotNil(try store.load())
        XCTAssertNotNil(try directory.read(NativeOwnership.intentName))
        let retry = try await controller.previewLocalSetupRemoval()
        let result = try await controller.applyLocalSetupRemoval(previewToken: retry.preview_token)
        XCTAssertTrue(result.keychain_session_absent)
        XCTAssertNil(try directory.read(NativeOwnership.intentName))
        let counts = await transport.counts(); XCTAssertEqual(counts.1, 2)
    }

    func testJournalSyncFailureStopsBeforeDeletingConfirmedSession() async throws {
        let (_, store, transport, _) = try fixture()
        let synchronization = JournalSyncFault(directory: directory, failureAt: 2)
        let controller = SessionController(directory: directory, store: store, transport: transport,
            removalJournalSync: synchronization.synchronize)
        let preview = try await controller.previewLocalSetupRemoval()
        do { _ = try await controller.applyLocalSetupRemoval(previewToken: preview.preview_token); XCTFail("Expected sync failure") }
        catch { XCTAssertEqual(error as? ClientError, .storageUnavailable) }
        XCTAssertNotNil(try store.load())
        XCTAssertNotNil(try directory.read("profile.json"))
        XCTAssertNotNil(try directory.read(NativeOwnership.intentName))
        let counts = await transport.counts(); XCTAssertEqual(counts.1, 1)
        let restarted = SessionController(directory: directory, store: store, transport: transport)
        let retry = try await restarted.previewLocalSetupRemoval()
        let result = try await restarted.applyLocalSetupRemoval(previewToken: retry.preview_token)
        XCTAssertTrue(result.server_revocation_confirmed)
        XCTAssertNil(try store.load())
        let after = await transport.counts(); XCTAssertEqual(after.1, 1)
    }

    func testInterruptedRemovalRetainsLaterEditedLauncherAndChangedProfileOnRestart() async throws {
        let (_, store, _, profile) = try fixture()
        let plan = try await launcher(profile)
        _ = try await ContextOptimizationSettings.save(enabled: true, profile: profile, directory: directory)
        var files = try LocalSetupRemoval.selection(directory, record: store.load())
        let partial = try XCTUnwrap(files.first { $0.kind == "preference" })
        XCTAssertTrue(try directory.removeExpected(partial.name, digest: partial.digest))
        files.removeAll { $0.name == partial.name }
        let intent = NativeRemovalIntent(schemaVersion: 1,
            sessionFingerprint: try NativeOwnership.sessionFingerprint(store.load()), profileID: profile.id,
            sessionWasPresent: true, revoked: true, files: files, removedCount: 1)
        try LocalSetupRemoval.save(intent, directory: directory)
        try store.delete()
        let editedLauncher = Data("customer edited launcher\n".utf8)
        try directory.write(editedLauncher, to: plan.launcher.lastPathComponent,
                            expected: directory.read(plan.launcher.lastPathComponent))
        var changed = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(profile)) as? [String: Any])
        changed["id"] = UUID().uuidString
        let editedProfile = try JSONSerialization.data(withJSONObject: changed, options: [.prettyPrinted, .sortedKeys])
        try directory.write(editedProfile, to: "profile.json", expected: directory.read("profile.json"))
        let restarted = SessionController(directory: directory, store: store,
                                          transport: FixtureTransport(clock: TestClock()))
        let preview = try await restarted.previewLocalSetupRemoval()
        let result = try await restarted.applyLocalSetupRemoval(previewToken: preview.preview_token)
        XCTAssertEqual(result.status, "complete_with_retained_files")
        XCTAssertEqual(result.retained_files, 3)
        XCTAssertEqual(try directory.read(plan.launcher.lastPathComponent), editedLauncher)
        XCTAssertEqual(try directory.read("profile.json"), editedProfile)
        XCTAssertNotNil(try directory.read(NativeOwnership.retainedFileName))
        XCTAssertNil(try directory.read(NativeOwnership.intentName))
        let verified = try await restarted.verifyLocalSetupRemoval()
        XCTAssertTrue(verified.generated_setup_absent)
        XCTAssertFalse(verified.server_revocation_confirmed)
    }

    func testEditedOriginalWithAmbiguousStageRemainsRefusedAndIntact() async throws {
        let (_, store, _, profile) = try fixture()
        let plan = try await launcher(profile)
        let selected = try LocalSetupRemoval.selection(directory, record: store.load())
        let entry = try XCTUnwrap(selected.first { $0.kind == "launcher" })
        let intent = NativeRemovalIntent(schemaVersion: 1, sessionFingerprint: nil, profileID: nil,
            sessionWasPresent: false, revoked: false, files: selected, removedCount: 0)
        try LocalSetupRemoval.save(intent, directory: directory)
        try store.delete()
        let stage = ".remove-" + NativeOwnership.digest(Data(entry.name.utf8))
        let stageURL = try directory.fileURL(stage)
        XCTAssertEqual(rename(plan.launcher.path, stageURL.path), 0)
        let edit = Data("customer edit after staged file\n".utf8)
        try directory.write(edit, to: entry.name, expected: nil)
        let controller = SessionController(directory: directory, store: store,
                                          transport: FixtureTransport(clock: TestClock()))
        let preview = try await controller.previewLocalSetupRemoval()
        do { _ = try await controller.applyLocalSetupRemoval(previewToken: preview.preview_token); XCTFail("Expected ambiguous stage") }
        catch { XCTAssertEqual(error as? ClientError, .removalRecoveryRequired) }
        XCTAssertEqual(try directory.read(entry.name), edit)
        XCTAssertNotNil(try directory.read(stage))
        XCTAssertNotNil(try directory.read(NativeOwnership.intentName))
    }

    func testOwnedSymlinkAndHardlinkNeverDeleteTargets() async throws {
        let (_, _, _, profile) = try fixture()
        let plan = try await launcher(profile)
        let name = plan.launcher.lastPathComponent
        let target = temporary.appendingPathComponent("sentinel.txt")
        try directory.write(Data("synthetic sentinel".utf8), to: target.lastPathComponent, expected: nil)
        try FileManager.default.removeItem(at: plan.launcher)
        try FileManager.default.createSymbolicLink(at: plan.launcher, withDestinationURL: target)
        XCTAssertThrowsError(try LocalSetupRemoval.selection(directory, record: nil))
        try FileManager.default.removeItem(at: plan.launcher)
        XCTAssertEqual(link(target.path, plan.launcher.path), 0)
        XCTAssertThrowsError(try LocalSetupRemoval.selection(directory, record: nil))
        XCTAssertEqual(try Data(contentsOf: target), Data("synthetic sentinel".utf8))
    }

    func testAtomicExternalEditIsRestoredAndNotPurged() throws {
        let name = "profile.json", original = Data("original synthetic data".utf8), edited = Data("external edit".utf8)
        try directory.write(original, to: name, expected: nil)
        XCTAssertThrowsError(try directory.removeExpected(name, digest: NativeOwnership.digest(original)) {
            try self.directory.write(edited, to: name, expected: original)
        })
        XCTAssertEqual(try directory.read(name), edited)
    }

    func testRootSubstitutionCannotDeleteOutsidePinnedDirectory() throws {
        let original = Data("original".utf8), sentinel = Data("outside sentinel".utf8)
        try directory.write(original, to: "profile.json", expected: nil)
        let moved = temporary.deletingLastPathComponent().appendingPathComponent("moved-" + UUID().uuidString)
        let outside = temporary.deletingLastPathComponent().appendingPathComponent("outside-" + UUID().uuidString)
        defer {
            try? FileManager.default.removeItem(at: moved)
            try? FileManager.default.removeItem(at: outside)
        }
        let outsideDirectory = try PrivateDirectory(root: outside)
        try outsideDirectory.write(sentinel, to: "profile.json", expected: nil)
        XCTAssertThrowsError(try directory.removeExpected("profile.json", digest: NativeOwnership.digest(original)) {
            try FileManager.default.moveItem(at: self.temporary, to: moved)
            try FileManager.default.createSymbolicLink(at: self.temporary, withDestinationURL: outside)
        })
        XCTAssertEqual(try outsideDirectory.read("profile.json"), sentinel)
        XCTAssertEqual(try PrivateDirectory(root: moved).read("profile.json"), original)
    }

    func testInterruptedStageResumesFromDurableIntent() async throws {
        let (controller, store, _, _) = try fixture()
        try store.delete()
        let selected = try LocalSetupRemoval.selection(directory, record: nil)
        let intent = NativeRemovalIntent(schemaVersion: 1, sessionFingerprint: nil, profileID: nil,
            sessionWasPresent: false, revoked: false, files: selected, removedCount: 0)
        try LocalSetupRemoval.save(intent, directory: directory)
        let file = try XCTUnwrap(selected.first)
        let stage = ".remove-" + NativeOwnership.digest(Data(file.name.utf8))
        let originalURL = try directory.fileURL(file.name), stageURL = try directory.fileURL(stage)
        XCTAssertEqual(rename(originalURL.path, stageURL.path), 0)
        let preview = try await controller.previewLocalSetupRemoval()
        _ = try await controller.applyLocalSetupRemoval(previewToken: preview.preview_token)
        XCTAssertNil(try directory.read(stage))
        XCTAssertNil(try directory.read(NativeOwnership.intentName))
    }

    func testDetectedLegacyOwnerRefusesWithoutMutations() async throws {
        let (_, store, transport, _) = try fixture()
        let controller = SessionController(directory: directory, store: store, transport: transport,
                                          removalSafety: { throw ClientError.profileBusy })
        let preview = try await controller.previewLocalSetupRemoval()
        do { _ = try await controller.applyLocalSetupRemoval(previewToken: preview.preview_token); XCTFail("Expected busy owner") }
        catch { XCTAssertEqual(error as? ClientError, .profileBusy) }
        XCTAssertNil(try directory.read(NativeOwnership.intentName))
        XCTAssertEqual(try store.load()?.state, .active)
        let counts = await transport.counts(); XCTAssertEqual(counts.1, 0)
    }

    func testPreviewTokenChangesWithOwnedSnapshotAndContainsNoSecret() async throws {
        let (controller, _, _, profile) = try fixture()
        let first = try await controller.previewLocalSetupRemoval()
        _ = try await launcher(profile)
        let second = try await controller.previewLocalSetupRemoval()
        XCTAssertNotEqual(first.preview_token, second.preview_token)
        let encoded = String(decoding: try JSONEncoder().encode(second), as: UTF8.self)
        XCTAssertFalse(encoded.contains("hox_"))
        XCTAssertFalse(encoded.contains(profile.gateway))
        do { _ = try await controller.applyLocalSetupRemoval(previewToken: first.preview_token); XCTFail("Expected changed preview") }
        catch { XCTAssertEqual(error as? ClientError, .configurationChanged) }
    }

    func testAppearanceResetTouchesOnlySelectedKeysWithInMemoryStore() {
        final class MemoryDefaults: NativeAppearanceStore {
            var keys = Set(NativeAppearanceSettings.keys + ["unrelated"])
            func removeObject(forKey key: String) { keys.remove(key) }
        }
        let defaults = MemoryDefaults()
        NativeAppearanceSettings.reset(defaults)
        XCTAssertEqual(defaults.keys, Set(["unrelated"]))
    }

    func testSharedGateSerializesDifferentStateRootsBeforeNewSignIn() async throws {
        let (_, store, _, profile) = try fixture()
        let clock = TestClock(), paused = PausedRemovalLogout(base: FixtureTransport(clock: TestClock()))
        let gateRoot = try PrivateDirectory(root: temporary.appendingPathComponent("shared-gate"))
        let gate = NativeSessionCoordination(directory: gateRoot)
        let first = SessionController(directory: directory, store: store, transport: paused, coordination: gate)
        let secondRoot = try PrivateDirectory(root: temporary.appendingPathComponent("second-state"))
        let second = SessionController(directory: secondRoot, store: store, transport: FixtureTransport(clock: TestClock()),
                                       now: clock.now, coordination: gate)
        let preview = try await first.previewLocalSetupRemoval()
        let removal = Task { try await first.applyLocalSetupRemoval(previewToken: preview.preview_token) }
        var entered = false
        for _ in 0..<50 {
            if await paused.isEntered() { entered = true; break }
            try await Task.sleep(for: .milliseconds(20))
        }
        XCTAssertTrue(entered)
        let next = try self.profile(client: .claudeCode)
        let login = Task { try await second.signIn(profile: next) { _ in } }
        try await Task.sleep(for: .milliseconds(50))
        XCTAssertEqual(try store.load()?.profile, profile)
        await paused.release()
        _ = try await removal.value
        try await login.value
        XCTAssertEqual(try store.load()?.profile, next)
        XCTAssertEqual(try secondRoot.loadProfile(), next)
    }
}

/// A deterministic fake-store assertion: every absent-root custody read must
/// observe the injected namespace lock held, without relying on scheduling.
private final class GateCheckingSessions: SessionStore, @unchecked Sendable {
    let path: String
    private let mutex = NSLock()
    private var reads = 0
    init(path: String) { self.path = path }
    var guardedReads: Int { mutex.lock(); defer { mutex.unlock() }; return reads }
    func load() throws -> SessionRecord? {
        let descriptor = open(path, O_RDWR | O_NOFOLLOW | O_CLOEXEC)
        guard descriptor >= 0 else { throw ClientError.unsafeStorage }
        defer { close(descriptor) }
        if flock(descriptor, LOCK_EX | LOCK_NB) == 0 {
            flock(descriptor, LOCK_UN)
            throw ClientError.unsafeStorage
        }
        guard errno == EWOULDBLOCK || errno == EAGAIN else { throw ClientError.unsafeStorage }
        mutex.lock(); reads += 1; mutex.unlock()
        return nil
    }
    func save(_ record: SessionRecord) throws { throw ClientError.unsafeStorage }
    func delete() throws { throw ClientError.unsafeStorage }
}

private final class JournalSyncFault: @unchecked Sendable {
    let directory: PrivateDirectory
    let failureAt: Int
    private let mutex = NSLock()
    private var calls = 0
    init(directory: PrivateDirectory, failureAt: Int) { self.directory = directory; self.failureAt = failureAt }
    func synchronize() throws {
        mutex.lock(); calls += 1; let fail = calls == failureAt; mutex.unlock()
        if fail { throw ClientError.storageUnavailable }
        try directory.syncMetadata()
    }
}

private actor PausedRemovalLogout: GatewayTransport {
    let base: FixtureTransport
    var entered = false
    var continuation: CheckedContinuation<Void, Never>?
    init(base: FixtureTransport) { self.base = base }
    func isEntered() -> Bool { entered }
    func release() { continuation?.resume(); continuation = nil }
    func request(profile: ConnectionProfile, path: String, body: Data?, accessToken: String?) async throws -> GatewayReply {
        if path == "/v1/auth/logout" {
            await withCheckedContinuation { value in continuation = value; entered = true }
        }
        return try await base.request(profile: profile, path: path, body: body, accessToken: accessToken)
    }
}
