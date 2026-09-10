import XCTest
@testable import HormuzCompanionCore

final class GeometryTests: XCTestCase {
    func testReferenceAnchorsAndScaleFactors() {
        XCTAssertEqual(CompanionGeometry.points(117), 44, accuracy: 0.0001)
        XCTAssertEqual(CompanionGeometry.points(186), 69.9487, accuracy: 0.001)
        XCTAssertEqual(CompanionGeometry.points(117, uiScale: 1.25), 55, accuracy: 0.0001)
        XCTAssertEqual(CompanionGeometry.points(117, uiScale: 1.5), 66, accuracy: 0.0001)
    }

    func testOneTwoAndThreeCellHeights() {
        let lineHeight = 17.0
        let one = CompanionGeometry.shapeHeight(metricCount: 1, percentLineHeight: lineHeight)
        let two = CompanionGeometry.shapeHeight(metricCount: 2, percentLineHeight: lineHeight)
        let three = CompanionGeometry.shapeHeight(metricCount: 3, percentLineHeight: lineHeight)
        let pitch = CompanionGeometry.cellExtent(percentLineHeight: lineHeight)
            + CompanionGeometry.points(83.5)

        XCTAssertEqual(two - one, pitch, accuracy: 0.0001)
        XCTAssertEqual(three - two, pitch, accuracy: 0.0001)
    }

    func testRingCentersFollowCellPitch() {
        let lineHeight = 17.0
        let first = CompanionGeometry.ringCenterFromTop(index: 0, percentLineHeight: lineHeight)
        let second = CompanionGeometry.ringCenterFromTop(index: 1, percentLineHeight: lineHeight)
        let pitch = CompanionGeometry.cellExtent(percentLineHeight: lineHeight)
            + CompanionGeometry.points(83.5)
        XCTAssertEqual(second - first, pitch, accuracy: 0.0001)
    }

    func testTooltipOriginClampsInsideVisibleBounds() {
        XCTAssertEqual(
            CompanionGeometry.clampedTooltipOrigin(
                proposed: -30,
                tooltipLength: 180,
                visibleMinimum: 0,
                visibleMaximum: 900
            ),
            12
        )
        XCTAssertEqual(
            CompanionGeometry.clampedTooltipOrigin(
                proposed: 850,
                tooltipLength: 180,
                visibleMinimum: 0,
                visibleMaximum: 900
            ),
            708
        )
    }
}
