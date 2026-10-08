import HormuzClientCore
import XCTest

final class NativeSessionGateTests: XCTestCase {
    func testActiveSessionCannotOverrideLockedOrUnknownScreen() {
        // The active-session event updates console/login, not screen lock.
        for lock: Bool? in [true, nil] {
            XCTAssertTrue(NativeSessionGate.isBlocked(onConsole: false, loginDone: true, screenLocked: lock))
            XCTAssertTrue(NativeSessionGate.isBlocked(onConsole: true, loginDone: true, screenLocked: lock))
        }
        XCTAssertFalse(NativeSessionGate.isBlocked(onConsole: true, loginDone: true, screenLocked: false))
    }

    func testUnknownOrInactiveConsoleAndLoginFailClosedAfterUnlockHint() {
        for console: Bool? in [false, nil] {
            XCTAssertTrue(NativeSessionGate.isBlocked(onConsole: console, loginDone: true, screenLocked: false))
        }
        for login: Bool? in [false, nil] {
            XCTAssertTrue(NativeSessionGate.isBlocked(onConsole: true, loginDone: login, screenLocked: false))
        }
        XCTAssertTrue(NativeSessionGate.isBlocked(onConsole: nil, loginDone: nil, screenLocked: nil))
    }
}
