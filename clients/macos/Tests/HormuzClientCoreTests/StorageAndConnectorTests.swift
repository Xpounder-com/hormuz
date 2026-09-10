import Darwin
import XCTest
@testable import HormuzClientCore

final class StorageAndConnectorTests: PrivateStorageTestCase {
    private struct LegacyProfile: Codable, Equatable {
        let id: UUID
        let gateway: String
        let organization: String
        let issuer: String?
        let client: AIClient
        let model: String
        let allowLoopbackHTTP: Bool
    }

    func testSymlinksHardlinksAndPublicFilesAreRejectedWithoutChanges() throws {
        let external = temporary.appendingPathComponent("external")
        try Data("leave me alone".utf8).write(to: external)
        let linked = temporary.appendingPathComponent("profile.json")
        try FileManager.default.createSymbolicLink(at: linked, withDestinationURL: external)
        XCTAssertThrowsError(try directory.read("profile.json"))
        XCTAssertThrowsError(try directory.saveProfile(profile()))
        try FileManager.default.removeItem(at: linked)
        try FileManager.default.linkItem(at: external, to: linked)
        XCTAssertThrowsError(try directory.read("profile.json"))
        try FileManager.default.removeItem(at: linked)
        try Data("public".utf8).write(to: linked)
        chmod(linked.path, 0o644)
        XCTAssertThrowsError(try directory.read("profile.json"))
        XCTAssertEqual(try String(contentsOf: external), "leave me alone")
    }

    func testRootSymlinkAndPathTraversalAreRejected() throws {
        let linked = temporary.appendingPathComponent("linked")
        try FileManager.default.createSymbolicLink(at: linked, withDestinationURL: temporary)
        XCTAssertThrowsError(try PrivateDirectory(root: linked))
        for path in ["../file", "a/b", "..", "/tmp/file"] { XCTAssertThrowsError(try directory.fileURL(path)) }
    }

    func testLockIsBoundedAndCanBeTakenAfterRelease() async throws {
        let first = try await directory.lock()
        do { _ = try await directory.lock(timeout: 0.05); XCTFail("Expected a busy profile") }
        catch { XCTAssertEqual(error as? ClientError, .profileBusy) }
        first.unlock()
        let next = try await directory.lock(timeout: 0.05)
        next.unlock()
    }

    func testConnectorPreviewIsReadOnlyAndApplyIsIdempotent() async throws {
        let profile = try profile(client: .claudeCode)
        try directory.saveProfile(profile)
        let plan = try ConnectorPlan.preview(profile: profile, directory: directory,
                                             helper: URL(fileURLWithPath: "/Applications/Hormuz.app/Contents/MacOS/Hormuz"))
        XCTAssertFalse(FileManager.default.fileExists(atPath: plan.launcher.path))
        try await plan.apply(in: directory)
        let namesBefore = try FileManager.default.contentsOfDirectory(atPath: directory.root.path).sorted()
        let again = try ConnectorPlan.preview(profile: profile, directory: directory,
                                              helper: URL(fileURLWithPath: "/Applications/Hormuz.app/Contents/MacOS/Hormuz"))
        try await again.apply(in: directory)
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: directory.root.path).sorted(), namesBefore)
        XCTAssertEqual(plan.files.count, 1)
        XCTAssertTrue(plan.previewText.contains("hormuz-context"))
        XCTAssertTrue(plan.previewText.contains("context"))
        XCTAssertTrue(plan.previewText.contains("run"))
        XCTAssertTrue(plan.previewText.contains("--credential-helper"))
        XCTAssertFalse(plan.previewText.contains("hox_"))
    }

    func testHostedCodexAliasesProduceOnlyBoundCodexLaunchers() throws {
        for alias in ["openai-primary", "openai-secondary"] {
            let profile = try ConnectionProfile(
                gateway: "https://gateway.example.test",
                organization: "org-a",
                client: .codex,
                model: alias,
                setup: .openAIPilot
            )
            let plan = try ConnectorPlan.preview(
                profile: profile,
                directory: directory,
                helper: URL(
                    fileURLWithPath: "/Applications/Hormuz.app/Contents/MacOS/Hormuz"
                )
            )
            XCTAssertEqual(plan.profile.setup, .openAIPilot)
            XCTAssertEqual(plan.files.count, 1)
            XCTAssertTrue(plan.launcher.lastPathComponent.hasPrefix("codex-"))
            XCTAssertTrue(plan.previewText.contains("hormuz-context"))
            XCTAssertTrue(plan.previewText.contains("--profile"))
            XCTAssertTrue(plan.previewText.contains(profile.key))
            XCTAssertFalse(plan.previewText.contains("https://gateway.example.test"))
            XCTAssertFalse(plan.previewText.contains("model=\"\(alias)\""))
            XCTAssertFalse(plan.previewText.contains("ANTHROPIC_BASE_URL"))
        }
        XCTAssertEqual(
            try FileManager.default.contentsOfDirectory(atPath: directory.root.path), []
        )
    }

    func testMalformedHostedProfileCannotWriteConnectorFiles() throws {
        let value: [String: Any] = [
            "id": UUID().uuidString,
            "gateway": "https://gateway.example.test",
            "organization": "org-a",
            "client": "claude-code",
            "model": "openai-primary",
            "allowLoopbackHTTP": false,
            "setup": "openai-pilot",
        ]
        let data = try JSONSerialization.data(withJSONObject: value)
        XCTAssertThrowsError(try JSONDecoder().decode(ConnectionProfile.self, from: data))
        XCTAssertEqual(
            try FileManager.default.contentsOfDirectory(atPath: directory.root.path), []
        )
    }

    func testSaveLoadAndLegacyDecoderPreserveProfileIdentity() throws {
        let profile = try ConnectionProfile(
            gateway: "https://gateway.example.test",
            organization: "org-a",
            issuer: "https://issuer.example.test",
            client: .codex,
            model: "openai-primary",
            setup: .openAIPilot
        )
        try directory.saveProfile(profile)
        let loaded = try XCTUnwrap(directory.loadProfile())
        XCTAssertEqual(loaded, profile)
        XCTAssertEqual(loaded.key, profile.key)
        XCTAssertEqual(loaded.setup, .openAIPilot)

        let encoded = try JSONEncoder().encode(profile)
        let legacy = try JSONDecoder().decode(LegacyProfile.self, from: encoded)
        XCTAssertEqual(legacy.id, profile.id)
        XCTAssertEqual(legacy.gateway, profile.gateway)
        XCTAssertEqual(legacy.organization, profile.organization)
        XCTAssertEqual(legacy.issuer, profile.issuer)
        XCTAssertEqual(legacy.client, profile.client)
        XCTAssertEqual(legacy.model, profile.model)
        XCTAssertEqual(legacy.allowLoopbackHTTP, profile.allowLoopbackHTTP)
    }

    func testAnEditAfterPreviewIsNotOverwritten() async throws {
        let profile = try profile()
        try directory.saveProfile(profile)
        let plan = try ConnectorPlan.preview(profile: profile, directory: directory,
                                             helper: URL(fileURLWithPath: "/Applications/Hormuz.app/Contents/MacOS/Hormuz"))
        let ownChange = Data("# my local change\n".utf8)
        try directory.write(ownChange, to: plan.files[0].name, expected: nil)
        do { try await plan.apply(in: directory); XCTFail("Expected stale preview rejection") }
        catch { XCTAssertEqual(error as? ClientError, .configurationChanged) }
        XCTAssertEqual(try directory.read(plan.files[0].name), ownChange)
    }

    func testEditAtAtomicExchangeBoundaryIsRestored() throws {
        let name = "connector.json"
        let preview = Data("preview\n".utf8)
        let replacement = Data("hormuz\n".utf8)
        let externalEdit = Data("external edit\n".utf8)
        try directory.write(preview, to: name, expected: nil)

        do {
            try directory.writeAtomically(
                replacement,
                to: name,
                expected: preview,
                beforeExchange: {
                    try externalEdit.write(to: self.directory.fileURL(name))
                }
            )
            XCTFail("Expected concurrent edit rejection")
        } catch {
            XCTAssertEqual(error as? ClientError, .configurationChanged)
        }

        XCTAssertEqual(try directory.read(name), externalEdit)
        XCTAssertFalse(
            try FileManager.default.contentsOfDirectory(atPath: directory.root.path)
                .contains(where: { $0.hasPrefix(".write-") })
        )
    }

    func testConfigurationUpdatesPreserveBackup() async throws {
        let profile = try profile()
        try directory.saveProfile(profile)
        let old = try ConnectorPlan.preview(profile: profile, directory: directory,
                                            helper: URL(fileURLWithPath: "/old/Hormuz"))
        try await old.apply(in: directory)
        let new = try ConnectorPlan.preview(profile: profile, directory: directory,
                                            helper: URL(fileURLWithPath: "/new/Hormuz"))
        try await new.apply(in: directory)
        let backups = try FileManager.default.contentsOfDirectory(atPath: directory.root.path).filter { $0.hasPrefix("backup-") }
        XCTAssertEqual(backups.count, 1)
        XCTAssertEqual(try directory.read(backups[0]), old.files[0].content)
    }

    func testShellQuotingProtectsHelperPathsAndLaunchDoesNotRewriteUserConfig() async throws {
        let profile = try profile()
        try directory.saveProfile(profile)
        let marker = temporary.appendingPathComponent("injected")
        let helper = URL(fileURLWithPath: "/Applications/O'Brien $(touch " + marker.path + ").app/Contents/MacOS/Hormuz")
        let contextHelper = try directory.fileURL("hormuz-context")
        try directory.write(Data("#!/bin/sh\ntest -z \"${OPENAI_API_KEY+x}\" || exit 9\nprintf '%s\\n' \"$@\"\n".utf8),
                            to: "hormuz-context", expected: nil, executable: true)
        let plan = try ConnectorPlan.preview(profile: profile, directory: directory, helper: helper,
                                             contextHelper: contextHelper)
        try await plan.apply(in: directory)
        let userSettings = temporary.appendingPathComponent("config.toml")
        let original = Data("# Personal config\nmodel = \"my-model\"\n".utf8)
        try original.write(to: userSettings)
        XCTAssertTrue(FileManager.default.isExecutableFile(atPath: contextHelper.path))
        let process = Process(), pipe = Pipe()
        process.executableURL = plan.launcher
        process.environment = ["PATH": directory.root.path + ":/usr/bin:/bin", "OPENAI_API_KEY": "synthetic-test-only"]
        process.standardOutput = pipe
        process.standardError = pipe
        try process.run()
        let output = pipe.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        XCTAssertEqual(process.terminationStatus, 0)
        let args = String(decoding: output, as: UTF8.self)
        XCTAssertTrue(args.contains("context\nrun\n--profile"))
        XCTAssertTrue(args.contains(helper.path))
        XCTAssertFalse(FileManager.default.fileExists(atPath: marker.path))
        XCTAssertEqual(try Data(contentsOf: userSettings), original)
        let attributes = try FileManager.default.attributesOfItem(atPath: plan.launcher.path)
        XCTAssertEqual((attributes[.posixPermissions] as? NSNumber)?.intValue, 0o700)
    }

    func testContextOptimizationDefaultsOffAndSharesClosedPrivateSetting() async throws {
        let profile = try profile()
        XCTAssertFalse(try ContextOptimizationSettings.load(profile: profile, directory: directory).enabled)
        let enabled = try await ContextOptimizationSettings.save(
            enabled: true, profile: profile, directory: directory
        )
        XCTAssertTrue(enabled.enabled)
        let name = ContextOptimizationSettings.fileName(profile: profile)
        let data = try XCTUnwrap(directory.read(name))
        let value = try JSONSerialization.jsonObject(with: data) as! [String: Any]
        XCTAssertEqual(value["schema_version"] as? Int, 1)
        XCTAssertEqual(value["enabled"] as? Bool, true)
        let attributes = try FileManager.default.attributesOfItem(atPath: directory.fileURL(name).path)
        XCTAssertEqual((attributes[.posixPermissions] as? NSNumber)?.intValue, 0o600)
    }

    func testContextOptimizationRejectsDuplicateUnknownAndNonBooleanValues() throws {
        let profile = try profile()
        let name = ContextOptimizationSettings.fileName(profile: profile)
        for body in [
            "{\"schema_version\":1,\"enabled\":true,\"enabled\":false}",
            "{\"schema_version\":1,\"enabled\":1}",
            "{\"schema_version\":1.0,\"enabled\":true}",
            "{\"enabled\":false,\"enabl\\u0065d\":true,\"schema_version\":1}",
            "{ \"enabled\": true, \"schema_version\": 1 }",
            "{\"schema_version\":1,\"enabled\":true,\"extra\":false}",
        ] {
            if try directory.read(name) == nil {
                try directory.write(Data(body.utf8), to: name, expected: nil)
            } else {
                try directory.write(Data(body.utf8), to: name, expected: try directory.read(name))
            }
            XCTAssertThrowsError(try ContextOptimizationSettings.load(profile: profile, directory: directory)) {
                XCTAssertEqual($0 as? ClientError, .contextSettingsInvalid)
            }
        }
    }
}
