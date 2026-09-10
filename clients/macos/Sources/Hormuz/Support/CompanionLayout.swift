import AppKit
import HormuzClientCore

struct CompanionLayout {
    let uiScale: CGFloat

    init(uiScale: Double) {
        self.uiScale = CGFloat(uiScale)
    }

    private func p(_ pixels: CGFloat) -> CGFloat {
        pixels * CGFloat(CompanionGeometry.designScale) * uiScale
    }

    var sideBodyDepth: CGFloat { p(186) }
    var curlRadius: CGFloat { p(103) }
    var cornerRadius: CGFloat { p(78.8) }
    var padTop: CGFloat { p(69.5) }
    var padBottom: CGFloat { p(50.1) }
    var cellSpacing: CGFloat { p(83.5) }

    var ringDiameter: CGFloat { p(117) }
    var trackStroke: CGFloat { p(15.5) }
    var progressStroke: CGFloat { p(8) }
    var glyphSize: CGFloat { p(46) }
    var ringLabelGap: CGFloat { p(26.9) }

    var pillWidth: CGFloat { p(26) }
    var pillHeight: CGFloat { p(210) }
    var pillHotZone: CGFloat { p(90) }

    var cardWidth: CGFloat { p(600) }
    var cardCorner: CGFloat { p(49.5) }
    var cardPadding: CGFloat { p(32) }
    var tailLength: CGFloat { p(75) }
    var tailHeight: CGFloat { p(87) }
    var tailGap: CGFloat { p(28) }
    var barHeight: CGFloat { p(10.5) }
    var headerGap: CGFloat { p(17) }
    var headerToBlock: CGFloat { p(21) }
    var labelToBar: CGFloat { p(16.8) }
    var barToUsed: CGFloat { p(17.8) }
    var blockSpacing: CGFloat { p(20) }

    var orbDiameter: CGFloat { p(124) }
    var orbStroke: CGFloat { p(18) }
    var orbGap: CGFloat { p(27) }
    var orbGlyph: CGFloat { p(56) }
    var orbHotZone: CGFloat { p(152) }
    var orbArcRadius: CGFloat { curlRadius - orbGap }

    var metricFontSize: CGFloat { p(27) / 0.714 }
    var cardTitleFontSize: CGFloat { p(26) / 0.714 }
    var cardBodyFontSize: CGFloat { p(18) / 0.714 }

    var metricLineHeight: CGFloat {
        lineHeight(NSFont.systemFont(ofSize: metricFontSize, weight: .semibold))
    }

    var cardTitleLineHeight: CGFloat {
        lineHeight(NSFont.systemFont(ofSize: cardTitleFontSize, weight: .semibold))
    }

    var cardBodyLineHeight: CGFloat {
        lineHeight(NSFont.systemFont(ofSize: cardBodyFontSize, weight: .regular))
    }

    var cellExtent: CGFloat { ringDiameter + ringLabelGap + metricLineHeight }
    var cellPitch: CGFloat { cellExtent + cellSpacing }

    func cellExtent(for reading: CompanionMetricReading) -> CGFloat {
        ringDiameter + (reading.showsHeadline ? ringLabelGap + metricLineHeight : 0)
    }

    func shapeHeight(metrics: [CompanionMetricReading]) -> CGFloat {
        2 * curlRadius + padTop + padBottom
            + metrics.reduce(0) { $0 + cellExtent(for: $1) }
            + CGFloat(max(0, metrics.count - 1)) * cellSpacing
    }

    func ringCenterFromTop(index: Int, metrics: [CompanionMetricReading]) -> CGFloat {
        curlRadius + padTop + ringDiameter / 2
            + metrics.prefix(index).reduce(0) { $0 + cellExtent(for: $1) + cellSpacing }
    }

    func bodyHeight(metricCount: Int) -> CGFloat {
        CGFloat(CompanionGeometry.bodyHeight(
            metricCount: metricCount,
            percentLineHeight: Double(metricLineHeight),
            uiScale: Double(uiScale)
        ))
    }

    func shapeHeight(metricCount: Int) -> CGFloat {
        CGFloat(CompanionGeometry.shapeHeight(
            metricCount: metricCount,
            percentLineHeight: Double(metricLineHeight),
            uiScale: Double(uiScale)
        ))
    }

    func ringCenterFromTop(index: Int) -> CGFloat {
        CGFloat(CompanionGeometry.ringCenterFromTop(
            index: index,
            percentLineHeight: Double(metricLineHeight),
            uiScale: Double(uiScale)
        ))
    }

    func cardHeight(for reading: CompanionMetricReading?) -> CGFloat {
        guard let reading else { return p(474) }
        if reading.blocks.isEmpty { return p(430) }
        if reading.message != nil { return p(550) }
        return p(500)
    }

    var panelShadowInset: CGFloat { max(8 * uiScale, p(32)) }
    var tooltipPanelWidth: CGFloat { panelShadowInset * 2 + cardWidth + tailLength }

    func tooltipPanelHeight(for reading: CompanionMetricReading?) -> CGFloat {
        panelShadowInset * 2 + cardHeight(for: reading)
    }

    private func lineHeight(_ font: NSFont) -> CGFloat {
        ceil(font.ascender - font.descender + font.leading)
    }
}
