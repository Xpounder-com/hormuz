import XCTest
@testable import HormuzClientCore

final class ProfileTests: XCTestCase {
    func testRejectsCredentialURLsAndNonlocalHTTP() throws {
        for gateway in ["https://user:password@example.com", "https://example.com/path", "https://example.com?q=secret",
                        "http://example.com", "http://127.1", "http://127.0.0.1.attacker.test", "https://example.com\n"] {
            XCTAssertThrowsError(try ConnectionProfile(gateway: gateway, organization: "team", client: .codex,
                                                        model: "approved", allowLoopbackHTTP: true))
        }
        XCTAssertThrowsError(try ConnectionProfile(gateway: "http://127.0.0.1:8787", organization: "team", client: .codex, model: "approved"))
        let profile = try ConnectionProfile(gateway: "https://GATEWAY.example.com/", organization: "team", client: .codex, model: "approved")
        XCTAssertEqual(profile.gateway, "https://gateway.example.com")
    }

    func testInvalidModelCannotInjectShellOrConfiguration() {
        for model in ["-c", "x y", "x\nmodel=evil", "$(touch /tmp/example)", "x\"", "a;echo", ""] {
            XCTAssertThrowsError(try ConnectionProfile(gateway: "https://example.com", organization: "team", client: .codex, model: model))
        }
    }
}
