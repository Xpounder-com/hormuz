import XCTest
@testable import HormuzClientCore

final class KeychainTests: XCTestCase {
    func testIsolatedKeychainRoundTripAndDeletion() throws {
        guard ProcessInfo.processInfo.environment["HORMUZ_TEST_KEYCHAIN"] == "1" else {
            throw XCTSkip("Opt in to an isolated synthetic Keychain round trip with HORMUZ_TEST_KEYCHAIN=1")
        }
        // A unique test-owned service; never reads or modifies a real session.
        let store = KeychainSessionStore(service: "com.hormuz.mac.tests." + UUID().uuidString)
        defer { try? store.delete() }
        XCTAssertNil(try store.load())
        let profile = try ConnectionProfile(gateway: "https://fixture.example.test", organization: "fixture", client: .codex, model: "fixture")
        let record = try SessionRecord(profile: profile,
            accessToken: "hox_a_" + String(repeating: "a", count: 43),
            refreshToken: "hox_r_" + String(repeating: "b", count: 43),
            accessExpiresAt: Date().addingTimeInterval(600), sessionExpiresAt: Date().addingTimeInterval(3600))
        try store.save(record)
        XCTAssertTrue(try store.load()?.accessToken == record.accessToken)
        var suspended = record
        suspended.state = .revocationPending
        try store.save(suspended)
        XCTAssertEqual(try store.load()?.state, .revocationPending)
        try store.delete()
        XCTAssertNil(try store.load())
    }
}
