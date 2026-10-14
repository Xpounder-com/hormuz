import CHormuzRust
import Foundation
import HormuzClientCore
import HormuzRustBridge
import XCTest

private final class FixtureStore: SessionStore, @unchecked Sendable {
    private let lock = NSLock()
    private var record: SessionRecord?
    init(_ record: SessionRecord? = nil) { self.record = record }
    func load() throws -> SessionRecord? { lock.lock(); defer { lock.unlock() }; return record }
    func save(_ value: SessionRecord) throws { lock.lock(); record = value; lock.unlock() }
    func delete() throws { lock.lock(); record = nil; lock.unlock() }
}

final class OwnershipTests: XCTestCase {
    func temporary() throws -> URL {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("hormuz-rust-bridge-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false,
            attributes: [.posixPermissions: 0o700])
        return root
    }
    func testActuallyLinkedRustABIAndOwnedAllocation() throws {
        XCTAssertEqual(hormuz_ui_abi_version(), 1)
        let bytes = try XCTUnwrap(hormuz_ui_context_setting(true))
        defer { hormuz_ui_bytes_free(bytes) }
        var data = Data(count: hormuz_ui_bytes_length(bytes))
        XCTAssertTrue(data.withUnsafeMutableBytes { hormuz_ui_bytes_copy(bytes, $0.bindMemory(to: UInt8.self).baseAddress, $0.count) })
        XCTAssertEqual(String(decoding: data, as: UTF8.self), "{\"enabled\":true,\"schema_version\":1}")
    }
    @MainActor
    func testOneSubscriptionRepeatedCloseAndNoDeliveryAfterCancellation() async throws {
        let root = try temporary()
        defer { try? FileManager.default.removeItem(at: root) }
        let client = try await RustNativeClient.open(directory: root, store: FixtureStore(), openBrowser: { _ in false })
        for _ in 0..<12 {
            let received = expectation(description: "native initial projection")
            var count = 0
            let subscription = try await client.subscribe { data in
                XCTAssertFalse(data.isEmpty)
                count += 1
                if count == 1 { received.fulfill() }
            }
            do {
                _ = try await client.subscribe { _ in XCTFail("duplicate subscription delivered") }
                XCTFail("duplicate accepted")
            } catch RustBridgeError.busy {}
            await fulfillment(of: [received], timeout: 2)
            await subscription.cancel()
            let before = count
            try await client.cancel()
            _ = try await client.snapshot()
            await Task.yield()
            XCTAssertEqual(count, before)
        }
        await client.shutdown()
        do { _ = try await client.snapshot(); XCTFail("closed core used") }
        catch RustBridgeError.closed {}
    }
    func testSwiftCustodyRecordRoundTripAndCredentialFreeDisplay() async throws {
        let root = try temporary()
        defer { try? FileManager.default.removeItem(at: root) }
        let profile = try ConnectionProfile(gateway: "https://gateway.invalid", organization: "fixture", client: .codex, model: "fixture")
        let record = try SessionRecord(profile: profile, accessToken: "hox_a_" + String(repeating: "A", count: 43),
            refreshToken: "hox_r_" + String(repeating: "B", count: 43), accessExpiresAt: Date().addingTimeInterval(600),
            sessionExpiresAt: Date().addingTimeInterval(3600))
        let store = FixtureStore(record)
        let client = try await RustNativeClient.open(directory: root, store: store, openBrowser: { _ in XCTFail("browser opened during restore"); return false })
        var json: [String: Any] = [:]
        for _ in 0..<100 {
            json = try JSONSerialization.jsonObject(with: await client.snapshot()) as? [String: Any] ?? [:]
            if json["phase"] as? String == "ready" { break }
            try await Task.sleep(for: .milliseconds(5))
        }
        XCTAssertEqual(json["phase"] as? String, "ready")
        let native = try NativeDisplayState(data: await client.snapshot())
        XCTAssertEqual(native.connection?.profile, profile)
        XCTAssertTrue(native.connection?.hasSession == true)
        XCTAssertNil(native.dashboard)
        let displayed = String(decoding: try await client.snapshot(), as: UTF8.self)
        XCTAssertTrue(displayed.contains(profile.id.uuidString))
        for forbidden in [record.accessToken, record.refreshToken, "accessToken", "refreshToken"] {
            XCTAssertFalse(displayed.contains(forbidden))
        }
        XCTAssertEqual(try store.load()?.profile, profile)
        XCTAssertEqual(try store.load()?.accessToken, record.accessToken)
        await client.shutdown()
    }
    func testShellOwnedSettingsCallbackReceivesCanonicalNonsecretBytes() async throws {
        let root = try temporary()
        defer { try? FileManager.default.removeItem(at: root) }
        let client = try await RustNativeClient.open(directory: root, store: FixtureStore(), openBrowser: { _ in false })
        try await client.changeContextSetting(enabled: false) { data in
            XCTAssertEqual(String(decoding: data, as: UTF8.self), "{\"enabled\":false,\"schema_version\":1}")
        }
        await client.shutdown()
    }
    func testHostedAndPendingSwiftCustodyProfilesAreNotDowngraded() async throws {
        for state in [SessionState.active, .refreshPending, .revocationPending] {
            let root = try temporary()
            defer { try? FileManager.default.removeItem(at: root) }
            let profile = try ConnectionProfile(gateway: "https://gateway.invalid", organization: "fixture", client: .codex,
                model: "fixture", desktopManaged: true, desktopProfileVersion: 7)
            let record = try SessionRecord(profile: profile, accessToken: "hox_a_" + String(repeating: "A", count: 43),
                refreshToken: "hox_r_" + String(repeating: "B", count: 43), accessExpiresAt: Date().addingTimeInterval(600),
                sessionExpiresAt: Date().addingTimeInterval(3600), state: state)
            let store = FixtureStore(record)
            let client = try await RustNativeClient.open(directory: root, store: store, openBrowser: { _ in false })
            var native = try NativeDisplayState(data: await client.snapshot())
            for _ in 0..<100 {
                if native.phase != "checking" { break }
                try await Task.sleep(for: .milliseconds(5))
                native = try NativeDisplayState(data: await client.snapshot())
            }
            XCTAssertEqual(native.connection?.profile, profile)
            XCTAssertEqual(native.connection?.sessionState, state)
            XCTAssertEqual(try store.load()?.profile.desktopProfileVersion, 7)
            XCTAssertEqual(try store.load()?.state, state)
            XCTAssertEqual(try store.load()?.accessToken, record.accessToken)
            await client.shutdown()
        }
    }
}
