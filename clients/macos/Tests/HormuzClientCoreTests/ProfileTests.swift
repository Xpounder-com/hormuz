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

    func testLegacyProfilesDecodeAsCustomWithoutChangingIdentity() throws {
        let id = try XCTUnwrap(UUID(uuidString: "e1a4d26d-d799-4a25-bb8c-62f8e583e2cc"))
        for client in AIClient.allCases {
            let value: [String: Any] = [
                "id": id.uuidString,
                "gateway": "https://gateway.example.test",
                "organization": "team",
                "issuer": "https://issuer.example.test",
                "client": client.rawValue,
                "model": "approved-alias",
                "allowLoopbackHTTP": false,
            ]
            let data = try JSONSerialization.data(withJSONObject: value)
            let profile = try JSONDecoder().decode(ConnectionProfile.self, from: data)
            XCTAssertEqual(profile.setup, .custom)
            XCTAssertEqual(profile.id, id)
            XCTAssertEqual(profile.key, id.uuidString.lowercased())
            XCTAssertEqual(profile.client, client)
            XCTAssertEqual(profile.model, "approved-alias")
            XCTAssertEqual(profile.organization, "team")
            XCTAssertEqual(profile.issuer, "https://issuer.example.test")
        }
    }

    func testHostedProfileRoundTripsWithNonSecretSetupMetadata() throws {
        let id = try XCTUnwrap(UUID(uuidString: "1b9dc401-580b-41d0-902c-b3cc227c76ef"))
        let profile = try ConnectionProfile(
            id: id,
            gateway: "https://GATEWAY.example.test/",
            organization: "team",
            issuer: "https://issuer.example.test",
            client: .codex,
            model: "openai-secondary",
            setup: .openAIPilot
        )
        let data = try JSONEncoder().encode(profile)
        let json = try XCTUnwrap(
            JSONSerialization.jsonObject(with: data) as? [String: Any]
        )
        XCTAssertEqual(json["setup"] as? String, "openai-pilot")
        XCTAssertNil(json["provider_api_key"])
        XCTAssertNil(json["session_token"])
        XCTAssertEqual(try JSONDecoder().decode(ConnectionProfile.self, from: data), profile)
        XCTAssertEqual(try profile.validated(), profile)
        XCTAssertEqual(profile.gateway, "https://gateway.example.test")
        XCTAssertEqual(profile.key, id.uuidString.lowercased())
    }

    func testPresentInvalidSetupNeverFallsBackToCustom() throws {
        let profile = try ConnectionProfile(
            gateway: "https://gateway.example.test",
            organization: "team",
            client: .codex,
            model: "approved-alias"
        )
        let base = try XCTUnwrap(
            JSONSerialization.jsonObject(with: JSONEncoder().encode(profile))
                as? [String: Any]
        )
        for invalid: Any in ["future", NSNull(), 2, true] {
            var value = base
            value["setup"] = invalid
            let data = try JSONSerialization.data(withJSONObject: value)
            XCTAssertThrowsError(
                try JSONDecoder().decode(ConnectionProfile.self, from: data),
                "Accepted invalid setup value: \(invalid)"
            )
        }
    }

    func testHostedSetupEnforcesCodexAliasesAndHTTPS() throws {
        for alias in ["openai-primary", "openai-secondary"] {
            let profile = try ConnectionProfile(
                gateway: "https://gateway.example.test",
                organization: "team",
                client: .codex,
                model: alias,
                setup: .openAIPilot
            )
            XCTAssertEqual(profile.setup, .openAIPilot)
            XCTAssertEqual(profile.model, alias)
            XCTAssertFalse(profile.allowLoopbackHTTP)
        }

        XCTAssertThrowsError(
            try ConnectionProfile(
                gateway: "https://gateway.example.test",
                organization: "team",
                client: .claudeCode,
                model: "openai-primary",
                setup: .openAIPilot
            )
        )
        for alias in ["approved-alias", "", "anthropic-primary"] {
            XCTAssertThrowsError(
                try ConnectionProfile(
                    gateway: "https://gateway.example.test",
                    organization: "team",
                    client: .codex,
                    model: alias,
                    setup: .openAIPilot
                )
            )
        }
        XCTAssertThrowsError(
            try ConnectionProfile(
                gateway: "http://127.0.0.1:8787",
                organization: "team",
                client: .codex,
                model: "openai-primary",
                allowLoopbackHTTP: true,
                setup: .openAIPilot
            )
        )
    }

    func testCustomSetupKeepsBothClientsAndApprovedAliases() throws {
        for client in AIClient.allCases {
            let profile = try ConnectionProfile(
                gateway: "http://127.0.0.1:8787",
                organization: "team",
                client: client,
                model: "team-approved:v2",
                allowLoopbackHTTP: true
            )
            XCTAssertEqual(profile.setup, .custom)
            XCTAssertEqual(profile.client, client)
            XCTAssertEqual(profile.model, "team-approved:v2")
        }
    }
}
