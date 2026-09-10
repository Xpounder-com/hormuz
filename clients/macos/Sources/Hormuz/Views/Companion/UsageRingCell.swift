import HormuzClientCore
import SwiftUI

struct UsageRingCell: View {
    let reading: CompanionMetricReading
    let layout: CompanionLayout
    let isSelected: Bool
    let onHoverChange: (Bool) -> Void
    let onActivate: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var band: CompanionUsageBand? {
        reading.usedFraction.map(CompanionUsageBand.band)
    }

    private var ringColor: Color {
        if let band {
            return switch band {
            case .ample: CompanionPalette.live
            case .watch: CompanionPalette.watch
            case .critical, .exhausted: CompanionPalette.critical
            }
        }
        return switch reading.status {
        case .current: CompanionPalette.live
        case .stale: CompanionPalette.watch
        case .offline: CompanionPalette.textSecondary
        case .needsAuthentication: CompanionPalette.critical
        }
    }

    private var sweep: CGFloat {
        CGFloat(min(max(reading.usedFraction ?? 0, 0), 1))
    }

    var body: some View {
        Button(action: onActivate) {
            VStack(spacing: layout.ringLabelGap) {
                ZStack {
                    Circle()
                        .strokeBorder(CompanionPalette.ringTrack, lineWidth: layout.trackStroke)

                    if reading.usedFraction != nil {
                        Circle()
                            .inset(by: layout.trackStroke / 2)
                            .trim(from: 0, to: sweep)
                            .stroke(
                                ringColor,
                                style: StrokeStyle(
                                    lineWidth: layout.progressStroke,
                                    lineCap: .round
                                )
                            )
                            .rotationEffect(.degrees(-90))
                            .animation(
                                CompanionMotion.respectingReduceMotion(
                                    CompanionMotion.reading,
                                    reduceMotion: reduceMotion
                                ),
                                value: sweep
                            )
                    } else {
                        Circle()
                            .fill(ringColor)
                            .frame(
                                width: layout.progressStroke + 2 * layout.uiScale,
                                height: layout.progressStroke + 2 * layout.uiScale
                            )
                            .offset(
                                x: layout.ringDiameter * 0.31,
                                y: -layout.ringDiameter * 0.31
                            )
                            .accessibilityHidden(true)
                    }

                    Image(systemName: reading.id.symbolName)
                        .font(.system(size: layout.glyphSize, weight: .medium))
                        .foregroundStyle(CompanionPalette.textPrimary)
                        .opacity(reading.status.isUnavailable ? 0.55 : 1)
                }
                .frame(width: layout.ringDiameter, height: layout.ringDiameter)

                if reading.showsHeadline {
                    Text(reading.headlineText)
                    .font(.system(size: layout.metricFontSize, weight: .semibold))
                    .monospacedDigit()
                    .minimumScaleFactor(0.72)
                    .lineLimit(1)
                    .foregroundStyle(CompanionPalette.textPrimary)
                    .frame(width: layout.sideBodyDepth - 8, height: layout.metricLineHeight)
                    .contentTransition(.numericText())
                }
            }
        }
        .buttonStyle(.plain)
        .frame(width: layout.sideBodyDepth, height: layout.cellExtent(for: reading))
        .opacity(reading.status.isStale ? 0.55 : 1)
        .scaleEffect(isSelected ? 1.025 : 1)
        .contentShape(Rectangle())
        .onHover(perform: onHoverChange)
        .accessibilityLabel("\(reading.id.displayName), \(reading.status.accessibilityLabel)")
        .accessibilityValue(reading.showsHeadline ? reading.headlineText : "Unavailable")
        .accessibilityHint("Click to pin or unpin details")
    }
}

private extension CompanionReadingStatus {
    var accessibilityLabel: String {
        switch self {
        case .current: "gateway verified"
        case .stale: "refreshing"
        case .offline: "gateway unavailable"
        case .needsAuthentication: "authentication required"
        }
    }
}
