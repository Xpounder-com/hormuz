import Foundation
import XCTest
@testable import HormuzClientCore

final class SharedContractTests: PrivateStorageTestCase {
    func testSnapshotInputsPreserveExistingIdentityUsageAndCostLabels() throws {
        let inputs = try fixture("snapshots")
        let profile = try JSONDecoder().decode(ConnectionProfile.self,
            from: JSONSerialization.data(withJSONObject: XCTUnwrap(inputs["profile"])))
        let identity = try GatewayJSON.decoder().decode(GatewayIdentity.self,
            from: JSONSerialization.data(withJSONObject: XCTUnwrap(inputs["identity"])))
        try identity.validate(for: profile)
        for name in ["usage", "zero"] {
            let usage = try GatewayJSON.decoder().decode(PersonalUsage.self,
                from: JSONSerialization.data(withJSONObject: XCTUnwrap(inputs[name])))
            try usage.validate()
            XCTAssertEqual(usage.costBasis, "configured_rate_card_estimate")
            XCTAssertEqual(usage.coverage, "gateway_captured_requests_only")
            if name == "zero" { XCTAssertEqual(usage.requests, 0); XCTAssertEqual(usage.inputTokens, 0) }
        }
    }

    func testSharedCredentialRecordCodecAndSessionTransitions() async throws {
        let vectors = try fixture("sessions")
        let source = try XCTUnwrap(vectors["record"] as? [String: Any])
        let sourceData = try JSONSerialization.data(withJSONObject: source)
        let original = try JSONDecoder().decode(SessionRecord.self, from: sourceData)
        let encoded = try JSONEncoder().encode(original)
        let roundTrip = try JSONDecoder().decode(SessionRecord.self, from: encoded)
        XCTAssertEqual(roundTrip.accessExpiresAt, original.accessExpiresAt)
        XCTAssertEqual(roundTrip.sessionExpiresAt, original.sessionExpiresAt)
        XCTAssertEqual(roundTrip.profile, original.profile)
        let output = try XCTUnwrap(JSONSerialization.jsonObject(with: encoded) as? [String: Any])
        XCTAssertEqual(output as NSDictionary, source as NSDictionary)
        for item in try XCTUnwrap(vectors["cases"] as? [[String: Any]]) {
            let id = try XCTUnwrap(item["id"] as? String)
            let clock = TestClock(), store = MemorySessions()
            let transport = FixtureTransport(clock: clock)
            let state = try XCTUnwrap(SessionState(rawValue: XCTUnwrap(item["state"] as? String)))
            let saved = try SessionRecord(profile: original.profile, accessToken: original.accessToken,
                refreshToken: original.refreshToken,
                accessExpiresAt: clock.now().addingTimeInterval(XCTUnwrap(item["access_remaining"] as? Double)),
                sessionExpiresAt: clock.now().addingTimeInterval(XCTUnwrap(item["session_remaining"] as? Double)), state: state)
            try store.save(saved)
            try directory.saveProfile(saved.profile)
            let controller = SessionController(directory: directory, store: store, transport: transport, now: { clock.now() })
            do {
                _ = try await controller.accessCredential(profileID: saved.profile.id)
                XCTAssertEqual(item["result"] as? String, "credential", id)
            } catch {
                XCTAssertEqual((error as? ClientError)?.rawValue, item["result"] as? String, id)
            }
            let counts = await transport.counts()
            XCTAssertEqual(counts.0, item["refreshes"] as? Int, id)
        }
    }

    private func fixture(_ name: String) throws -> [String: Any] {
        var root = URL(fileURLWithPath: #filePath)
        for _ in 0..<5 { root.deleteLastPathComponent() }
        let data = try Data(contentsOf: root.appendingPathComponent("tests/fixtures/native_client/v1/\(name).json"))
        let fixture = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(fixture["schema_id"] as? String, "hormuz.native-client-fixtures")
        XCTAssertEqual(fixture["schema_version"] as? Int, 1)
        return fixture
    }

    func testSharedProfileValidationAndNormalization() throws {
        let cases = try XCTUnwrap(fixture("profiles")["cases"] as? [[String: Any]])
        XCTAssertEqual(cases.count, 49)
        for item in cases {
            let id = try XCTUnwrap(item["id"] as? String)
            let valid = try XCTUnwrap(item["swift_valid"] as? Bool)
            let input = try JSONSerialization.data(withJSONObject: XCTUnwrap(item["input"]))
            var output: [String: Any]?
            do {
                let profile = try JSONDecoder().decode(ConnectionProfile.self, from: input)
                let encoded = try JSONEncoder().encode(profile)
                output = try XCTUnwrap(JSONSerialization.jsonObject(with: encoded) as? [String: Any])
                XCTAssertEqual(try JSONDecoder().decode(ConnectionProfile.self, from: encoded), profile, id)
                XCTAssertEqual(profile.key, profile.id.uuidString.lowercased(), id)
            } catch { XCTAssertFalse(valid, "Existing Mac profile rejected \(id): \(error)") }
            XCTAssertEqual(output != nil, valid, id)
            if let output {
                let expected = try XCTUnwrap(item["expected"] as? [String: Any])
                XCTAssertEqual(output as NSDictionary, expected as NSDictionary, id)
            }
        }
    }

    func testSharedContextSettingBytesAgainstExistingPrivateFileReader() throws {
        let cases = try XCTUnwrap(fixture("context-settings")["cases"] as? [[String: Any]])
        XCTAssertEqual(cases.count, 20)
        let profile = try profile()
        let name = ContextOptimizationSettings.fileName(profile: profile)
        for item in cases {
            let id = try XCTUnwrap(item["id"] as? String)
            let previous = try directory.read(name)
            if let raw = item["file_bytes"] as? String {
                try directory.write(Data(raw.utf8), to: name, expected: previous)
            } else {
                // Missing is the first fixture; exercise the actual missing-file path.
                XCTAssertNil(previous, id)
            }
            var actual: Bool?
            do {
                actual = try ContextOptimizationSettings.load(profile: profile, directory: directory).enabled
            } catch { XCTAssertEqual(error as? ClientError, .contextSettingsInvalid, id) }
            XCTAssertEqual(actual, item["expected_enabled"] as? Bool, id)
        }
    }

    func testSharedSessionFreshnessAndContextStatusMeaning() throws {
        let fixture = try fixture("status")
        for item in try XCTUnwrap(fixture["session_states"] as? [[String: Any]]) {
            let state = try (item["code"] as? String).map {
                try JSONDecoder().decode(SessionState.self, from: JSONEncoder().encode($0))
            }
            let status = ConnectionStatus(profile: try profile(), sessionState: state, expiresAt: nil)
            XCTAssertEqual(status.hasSession, item["has_session"] as? Bool)
            XCTAssertEqual(state?.rawValue, item["code"] as? String)
        }
        for code in try XCTUnwrap(fixture["rejected_session_states"] as? [String]) {
            XCTAssertThrowsError(try JSONDecoder().decode(SessionState.self, from: JSONEncoder().encode(code)))
        }
        for item in try XCTUnwrap(fixture["reading_states"] as? [[String: Any]]) {
            let code = try XCTUnwrap(item["code"] as? String)
            let status: CompanionReadingStatus
            switch code {
            case "current": status = .current
            case "stale": status = .stale
            case "offline": status = .offline
            case "needsAuthentication": status = .needsAuthentication
            default: XCTFail("Unknown fixture status: \(code)"); continue
            }
            XCTAssertEqual(status.isStale, item["is_stale"] as? Bool, code)
            XCTAssertEqual(status.isUnavailable, item["is_unavailable"] as? Bool, code)
        }
        for item in try XCTUnwrap(fixture["context_states"] as? [[String: Any]]) {
            let state = try XCTUnwrap(ContextOptimizationStatus(rawValue: XCTUnwrap(item["code"] as? String)))
            XCTAssertEqual(state.label, item["label"] as? String)
        }
        XCTAssertNil(ContextOptimizationStatus(rawValue: "unknown"))
    }

    func testSharedFixedErrorCatalog() throws {
        let cases = try XCTUnwrap(fixture("errors")["cases"] as? [[String: Any]])
        XCTAssertEqual(cases.count, 22)
        for item in cases {
            let code = try XCTUnwrap(item["code"] as? String)
            let error = try XCTUnwrap(ClientError(rawValue: code))
            XCTAssertEqual(error.errorDescription, item["swift_message"] as? String, code)
            XCTAssertEqual(ClientError.message(for: error), item["swift_message"] as? String, code)
        }
        XCTAssertNil(ClientError(rawValue: "arbitrary server response"))
    }

    func testRawJSONCountersPreserveExactIntegerValues() throws {
        var root = URL(fileURLWithPath: #filePath)
        for _ in 0..<5 { root.deleteLastPathComponent() }
        let data = try Data(contentsOf: root.appendingPathComponent("tests/fixtures/native_client/v1/raw-numbers.json"))
        let fixture = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        let cases = try XCTUnwrap(fixture["cases"] as? [[String: Any]])
        for item in cases {
            let id = try XCTUnwrap(item["id"] as? String)
            let raw = try XCTUnwrap(item["response_json"] as? String)
            let expected = (item["expected_requests"] as? String).flatMap(Int.init)
            var actual: Int?
            do {
                let usage = try GatewayJSON.decoder().decode(PersonalUsage.self, from: Data(raw.utf8))
                try usage.validate()
                actual = usage.requests
            } catch { }
            XCTAssertEqual(actual, expected, id)
        }
    }

    func testSharedGatewayVectorsAgainstExistingNativeModels() throws {
        var root = URL(fileURLWithPath: #filePath)
        for _ in 0..<5 { root.deleteLastPathComponent() }
        let data = try Data(contentsOf: root.appendingPathComponent("tests/fixtures/native_client/v1/gateway.json"))
        let fixture = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(fixture["schema_id"] as? String, "hormuz.native-client-fixtures")
        XCTAssertEqual(fixture["schema_version"] as? Int, 1)
        let cases = try XCTUnwrap(fixture["cases"] as? [[String: Any]])
        XCTAssertEqual(cases.count, 41)
        let profile = try ConnectionProfile(
            gateway: "https://gateway.example.test",
            organization: XCTUnwrap(fixture["organization"] as? String),
            client: XCTUnwrap(AIClient(rawValue: XCTUnwrap(fixture["client"] as? String))),
            model: "approved-alias"
        )
        for item in cases {
            let id = try XCTUnwrap(item["id"] as? String)
            let expectedValid = try XCTUnwrap(item["client_valid"] as? Bool)
            let response = try JSONSerialization.data(withJSONObject: XCTUnwrap(item["response"]))
            var output: [String: Any]?
            do {
                switch try XCTUnwrap(item["kind"] as? String) {
                case "usage":
                    let usage = try GatewayJSON.decoder().decode(PersonalUsage.self, from: response)
                    try usage.validate()
                    output = ["requests": usage.requests, "input_tokens": usage.inputTokens,
                              "output_tokens": usage.outputTokens, "cost_usd": usage.costUsd,
                              "cost_basis": usage.costBasis, "coverage": usage.coverage]
                case "identity":
                    let identity = try GatewayJSON.decoder().decode(GatewayIdentity.self, from: response)
                    try identity.validate(for: profile)
                    output = ["actor_id": identity.actorId, "actor_name": identity.actorName,
                              "organization_id": identity.organizationId]
                default:
                    XCTFail("Unknown fixture kind: \(id)")
                }
            } catch {
                XCTAssertFalse(expectedValid, "Existing native client rejected \(id)")
            }
            XCTAssertEqual(output != nil, expectedValid, id)
            if let output {
                let expected = try XCTUnwrap(item["expected"] as? [String: Any])
                XCTAssertEqual(output as NSDictionary, expected as NSDictionary, id)
            }
        }
    }
}
