import Combine
import XCTest
@testable import HormuzClientCore

final class CompanionTests: XCTestCase {
    @MainActor
    func testPanelUpdateReadsSettledPublishedScaleAndCoalescesRapidChanges() async throws {
        final class Scale: ObservableObject { @Published var value = 1.0 }
        let source = Scale()
        let updates = SettledPanelUpdate()
        var observed: [Double] = []
        let subscription = source.$value.dropFirst().sink { _ in
            updates.schedule { observed.append(source.value) }
        }
        source.value = 1.25
        source.value = 1.5
        XCTAssertTrue(observed.isEmpty)
        try await Task.sleep(nanoseconds: 20_000_000)
        XCTAssertEqual(observed, [1.5])
        source.value = 1
        try await Task.sleep(nanoseconds: 20_000_000)
        XCTAssertEqual(observed, [1.5, 1])
        subscription.cancel()
    }

    @MainActor
    func testStoppedPanelDoesNotReceivePendingUpdate() async throws {
        let updates = SettledPanelUpdate()
        var count = 0
        updates.schedule { count += 1 }
        updates.cancel()
        try await Task.sleep(nanoseconds: 20_000_000)
        XCTAssertEqual(count, 0)
    }

    @MainActor
    func testHubBackPreservesReviewAndSetupHierarchy() {
        let hub = EdgeHubNavigation()
        hub.open(.review)
        hub.back()
        XCTAssertEqual(hub.page, .client)
        hub.back()
        XCTAssertEqual(hub.page, .home)
        hub.back()
        XCTAssertFalse(hub.isOpen)
        hub.open(.setup)
        hub.back()
        XCTAssertEqual(hub.page, .connection)
        hub.close()
        XCTAssertFalse(hub.isOpen)
    }

    @MainActor
    func testHoverCannotReplacePinnedDetailsButClickCan() {
        let hover = HoverCoordinator()
        hover.showAndPin(.cost)
        hover.enterMetric(.tokens)
        XCTAssertEqual(hover.selectedMetric, .cost)
        hover.togglePin(.tokens)
        XCTAssertEqual(hover.selectedMetric, .tokens)
        XCTAssertEqual(hover.pinnedMetric, .tokens)
    }

    func testUsageBandBoundaries() {
        XCTAssertEqual(CompanionUsageBand.band(for: 0), .ample)
        XCTAssertEqual(CompanionUsageBand.band(for: 0.49), .ample)
        XCTAssertEqual(CompanionUsageBand.band(for: 0.50), .watch)
        XCTAssertEqual(CompanionUsageBand.band(for: 0.69), .watch)
        XCTAssertEqual(CompanionUsageBand.band(for: 0.70), .critical)
        XCTAssertEqual(CompanionUsageBand.band(for: 0.99), .critical)
        XCTAssertEqual(CompanionUsageBand.band(for: 1), .exhausted)
    }

    func testGeometryAddsExactlyOneCellPitchPerMetric() {
        let one = CompanionGeometry.bodyHeight(metricCount: 1, percentLineHeight: 15)
        let two = CompanionGeometry.bodyHeight(metricCount: 2, percentLineHeight: 15)
        let three = CompanionGeometry.bodyHeight(metricCount: 3, percentLineHeight: 15)
        let pitch = CompanionGeometry.cellExtent(percentLineHeight: 15)
            + CompanionGeometry.points(83.5)
        XCTAssertEqual(two - one, pitch, accuracy: 0.0001)
        XCTAssertEqual(three - two, pitch, accuracy: 0.0001)
    }

    func testTooltipOriginClampsToVisibleFrame() {
        XCTAssertEqual(
            CompanionGeometry.clampedTooltipOrigin(
                proposed: -100,
                tooltipLength: 180,
                visibleMinimum: 0,
                visibleMaximum: 500
            ),
            12
        )
        XCTAssertEqual(
            CompanionGeometry.clampedTooltipOrigin(
                proposed: 490,
                tooltipLength: 180,
                visibleMinimum: 0,
                visibleMaximum: 500
            ),
            308
        )
    }

    @MainActor
    func testPinnedTooltipSurvivesPointerExitUntilDismissed() async throws {
        let hover = HoverCoordinator(dismissalDelayNanoseconds: 1_000_000)
        hover.enterMetric(.tokens)
        hover.togglePin(.tokens)
        hover.leaveMetric(.tokens)
        try await Task.sleep(nanoseconds: 5_000_000)
        XCTAssertEqual(hover.selectedMetric, .tokens)
        XCTAssertEqual(hover.pinnedMetric, .tokens)
        hover.dismiss()
        XCTAssertNil(hover.selectedMetric)
        XCTAssertNil(hover.pinnedMetric)
    }

    @MainActor
    func testEnteringTooltipCancelsPendingDismissal() async throws {
        let hover = HoverCoordinator(dismissalDelayNanoseconds: 20_000_000)
        hover.enterMetric(.requests)
        hover.leaveMetric(.requests)
        hover.enterTooltip()
        try await Task.sleep(nanoseconds: 30_000_000)
        XCTAssertEqual(hover.selectedMetric, .requests)
        let dismissed = expectation(description: "tooltip dismissal completed")
        let subscription = hover.$selectedMetric.dropFirst().sink { selected in
            if selected == nil {
                dismissed.fulfill()
            }
        }
        hover.leaveTooltip()
        await fulfillment(of: [dismissed], timeout: 1)
        subscription.cancel()
        XCTAssertNil(hover.selectedMetric)
    }
}
