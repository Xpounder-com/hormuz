import Darwin
import XCTest
@testable import HormuzClientCore

final class StorageAndConnectorTests: PrivateStorageTestCase {
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
        let json = try JSONSerialization.jsonObject(with: plan.files.first!.content) as! [String: Any]
        XCTAssertNotNil(json["apiKeyHelper"])
        XCTAssertFalse(plan.previewText.contains("hox_"))
        XCTAssertFalse(plan.previewText.contains("settings.json' >"))
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
        let plan = try ConnectorPlan.preview(profile: profile, directory: directory, helper: helper)
        try await plan.apply(in: directory)
        let userSettings = temporary.appendingPathComponent("config.toml")
        let original = Data("# Personal config\nmodel = \"my-model\"\n".utf8)
        try original.write(to: userSettings)
        let stub = try directory.fileURL("codex")
        try directory.write(Data("#!/bin/sh\ntest -z \"${OPENAI_API_KEY+x}\" || exit 9\nprintf '%s\\n' \"$@\"\n".utf8), to: "codex", expected: nil, executable: true)
        XCTAssertTrue(FileManager.default.isExecutableFile(atPath: stub.path))
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
        XCTAssertTrue(args.contains("model_providers.hormuz_connector="))
        XCTAssertTrue(args.contains(helper.path))
        XCTAssertFalse(FileManager.default.fileExists(atPath: marker.path))
        XCTAssertEqual(try Data(contentsOf: userSettings), original)
        let attributes = try FileManager.default.attributesOfItem(atPath: plan.launcher.path)
        XCTAssertEqual((attributes[.posixPermissions] as? NSNumber)?.intValue, 0o700)
    }
}
