import XCTest
@testable import HormuzCompanionCore

@MainActor
final class HoverCoordinatorTests: XCTestCase {
    func testTooltipEntryCancelsPendingDismissal() async throws {
        let coordinator = HoverCoordinator(dismissalDelayNanoseconds: 20_000_000)
        coordinator.enterMetric(.budget)
        coordinator.leaveMetric(.budget)
        try await Task.sleep(nanoseconds: 5_000_000)
        coordinator.enterTooltip()
        try await Task.sleep(nanoseconds: 30_000_000)
        XCTAssertEqual(coordinator.selectedMetric, .budget)
    }

    func testSwitchingCellsCancelsEarlierDismissal() async throws {
        let coordinator = HoverCoordinator(dismissalDelayNanoseconds: 20_000_000)
        coordinator.enterMetric(.budget)
        coordinator.leaveMetric(.budget)
        coordinator.enterMetric(.tokens)
        try await Task.sleep(nanoseconds: 30_000_000)
        XCTAssertEqual(coordinator.selectedMetric, .tokens)
    }

    func testPinnedTooltipPersistsAfterHoverLeaves() async throws {
        let coordinator = HoverCoordinator(dismissalDelayNanoseconds: 10_000_000)
        coordinator.togglePin(.requests)
        coordinator.leaveMetric(.requests)
        try await Task.sleep(nanoseconds: 20_000_000)
        XCTAssertEqual(coordinator.selectedMetric, .requests)
        XCTAssertEqual(coordinator.pinnedMetric, .requests)
    }

    func testSecondActivationUnpinsAndAllowsDismissal() async throws {
        let coordinator = HoverCoordinator(dismissalDelayNanoseconds: 10_000_000)
        coordinator.togglePin(.budget)
        coordinator.togglePin(.budget)
        coordinator.leaveMetric(.budget)
        try await Task.sleep(nanoseconds: 20_000_000)
        XCTAssertNil(coordinator.selectedMetric)
        XCTAssertNil(coordinator.pinnedMetric)
    }
}
