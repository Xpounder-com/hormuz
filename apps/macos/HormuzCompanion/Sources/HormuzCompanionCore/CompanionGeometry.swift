import Foundation

public enum CompanionGeometry {
    public static let designScale = 44.0 / 117.0

    public static func points(_ designPixels: Double, uiScale: Double = 1) -> Double {
        designPixels * designScale * uiScale
    }

    public static func cellExtent(percentLineHeight: Double, uiScale: Double = 1) -> Double {
        points(117, uiScale: uiScale) + points(26.9, uiScale: uiScale) + percentLineHeight
    }

    public static func bodyHeight(
        metricCount: Int,
        percentLineHeight: Double,
        uiScale: Double = 1
    ) -> Double {
        guard metricCount > 0 else { return 0 }
        let cells = Double(metricCount) * cellExtent(
            percentLineHeight: percentLineHeight,
            uiScale: uiScale
        )
        let gaps = Double(max(metricCount - 1, 0)) * points(83.5, uiScale: uiScale)
        return points(69.5, uiScale: uiScale)
            + cells
            + gaps
            + points(50.1, uiScale: uiScale)
    }

    public static func shapeHeight(
        metricCount: Int,
        percentLineHeight: Double,
        uiScale: Double = 1
    ) -> Double {
        bodyHeight(
            metricCount: metricCount,
            percentLineHeight: percentLineHeight,
            uiScale: uiScale
        ) + 2 * points(103, uiScale: uiScale)
    }

    public static func ringCenterFromTop(
        index: Int,
        percentLineHeight: Double,
        uiScale: Double = 1
    ) -> Double {
        let first = points(103 + 69.5 + 117 / 2, uiScale: uiScale)
        let pitch = cellExtent(percentLineHeight: percentLineHeight, uiScale: uiScale)
            + points(83.5, uiScale: uiScale)
        return first + Double(index) * pitch
    }

    public static func clampedTooltipOrigin(
        proposed: Double,
        tooltipLength: Double,
        visibleMinimum: Double,
        visibleMaximum: Double,
        inset: Double = 12
    ) -> Double {
        let minimum = visibleMinimum + inset
        let maximum = max(minimum, visibleMaximum - inset - tooltipLength)
        return min(max(proposed, minimum), maximum)
    }
}
