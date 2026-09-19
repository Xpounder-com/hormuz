import Foundation
import XCTest
@testable import HormuzClientCore

final class SharedContractTests: XCTestCase {
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
