import Foundation
import HormuzClientCore
import XCTest

final class NativeDisplayStateTests: XCTestCase {
    private func projection(status: String, measured: Bool) throws -> [String: Any] {
        var root = URL(fileURLWithPath: #filePath)
        for _ in 0..<5 { root.deleteLastPathComponent() }
        let fixture = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf:
            root.appendingPathComponent("tests/fixtures/native_client/v1/snapshots.json"))) as? [String: Any])
        return ["schema_version": 1, "phase": "ready", "error": NSNull(),
            "connection": ["profile": try XCTUnwrap(fixture["profile"]), "session_state": "active", "expires_at_epoch_seconds": 1_800_000_000],
            "snapshot": ["scope": "current_actor", "identity": measured ? try XCTUnwrap(fixture["identity"]) : NSNull(),
                "reading": ["status": status, "usage": measured ? try XCTUnwrap(fixture["zero"]) : NSNull(),
                    "checked_at_epoch_seconds": measured ? 1_780_000_000 : NSNull()]]]
    }
    private func decode(_ value: [String: Any]) throws -> NativeDisplayState {
        try NativeDisplayState(data: JSONSerialization.data(withJSONObject: value))
    }
    func testAbsentIsNotZeroAndMeasuredZeroRemainsAuthoritative() throws {
        let absent = try decode(projection(status: "offline", measured: false))
        XCTAssertEqual(absent.readingStatus, .offline)
        XCTAssertNil(absent.dashboard)
        let zero = try decode(projection(status: "current", measured: true))
        XCTAssertEqual(zero.readingStatus, .current)
        XCTAssertEqual(zero.dashboard?.usage.requests, 0)
        XCTAssertEqual(zero.dashboard?.usage.inputTokens, 0)
        XCTAssertEqual(zero.dashboard?.checkedAt.timeIntervalSince1970, 1_780_000_000)
        XCTAssertThrowsError(try decode(projection(status: "current", measured: false)))
    }
    func testRetainedOfflineAndStaleDataNeverBecomeCurrent() throws {
        for (code, state) in [("stale", CompanionReadingStatus.stale), ("offline", .offline)] {
            let value = try decode(projection(status: code, measured: true))
            XCTAssertEqual(value.readingStatus, state)
            XCTAssertNotNil(value.dashboard)
            XCTAssertEqual(value.dashboard?.checkedAt.timeIntervalSince1970, 1_780_000_000)
        }
        XCTAssertThrowsError(try decode(projection(status: "needsAuthentication", measured: true)))
    }
    func testUnknownSchemaScopeErrorAndReadingCodesFailClosed() throws {
        for (field, invalid): (String, Any) in [("schema_version", 2), ("error", "server body"), ("phase", "future")] {
            var value = try projection(status: "offline", measured: false)
            value[field] = invalid
            XCTAssertThrowsError(try decode(value))
        }
        var value = try projection(status: "offline", measured: false)
        var snapshot = try XCTUnwrap(value["snapshot"] as? [String: Any])
        snapshot["scope"] = "organization"
        value["snapshot"] = snapshot
        XCTAssertThrowsError(try decode(value))
        XCTAssertThrowsError(try decode(projection(status: "future", measured: false)))
    }
}
