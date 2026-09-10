import XCTest
@testable import HormuzCompanionCore

final class UsageBandTests: XCTestCase {
    func testThresholdBoundaries() {
        XCTAssertEqual(CompanionUsageBand.band(for: 0), .ample)
        XCTAssertEqual(CompanionUsageBand.band(for: 0.49), .ample)
        XCTAssertEqual(CompanionUsageBand.band(for: 0.50), .watch)
        XCTAssertEqual(CompanionUsageBand.band(for: 0.69), .watch)
        XCTAssertEqual(CompanionUsageBand.band(for: 0.70), .critical)
        XCTAssertEqual(CompanionUsageBand.band(for: 0.99), .critical)
        XCTAssertEqual(CompanionUsageBand.band(for: 1.00), .exhausted)
    }

    func testUnknownDenominatorStaysUnknown() {
        let snapshot = DemoSnapshotSource.snapshot(for: .offline)
        XCTAssertTrue(snapshot.metrics.allSatisfy { $0.usedFraction == nil })
        XCTAssertTrue(snapshot.metrics.allSatisfy { $0.headlineText == "—" })
    }

    func testFixedDemoMetricOrdering() {
        let snapshot = DemoSnapshotSource.snapshot(for: .normal)
        XCTAssertEqual(snapshot.metrics.map(\.id), [.budget, .tokens, .requests])
        XCTAssertEqual(snapshot.metric(.budget)?.headlineText, "73%")
        XCTAssertEqual(snapshot.metric(.tokens)?.headlineText, "21%")
        XCTAssertEqual(snapshot.metric(.requests)?.headlineText, "52%")
    }
}
