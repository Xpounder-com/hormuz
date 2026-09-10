import HormuzCompanionCore
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

    private var bandColor: Color {
        switch band {
        case .ample: CompanionPalette.ample
        case .watch: CompanionPalette.watch
        case .critical, .exhausted: CompanionPalette.critical
        case nil: CompanionPalette.ringTrack
        }
    }

    private var sweep: CGFloat {
        CGFloat(min(max(reading.usedFraction ?? 0, 0), 1))
    }

    var body: some View {
        VStack(spacing: layout.ringLabelGap) {
            ZStack {
                Circle()
                    .strokeBorder(CompanionPalette.ringTrack, lineWidth: layout.trackStroke)

                if reading.usedFraction != nil {
                    Circle()
                        .inset(by: layout.trackStroke / 2)
                        .trim(from: 0, to: sweep)
                        .stroke(
                            bandColor,
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
                }

                Image(systemName: reading.id.symbolName)
                    .font(.system(size: layout.glyphSize, weight: .medium))
                    .foregroundStyle(CompanionPalette.textPrimary)
                    .opacity(band == .exhausted ? 0.35 : 1)
            }
            .frame(width: layout.ringDiameter, height: layout.ringDiameter)

            Text(reading.headlineText)
                .font(.system(size: layout.percentFontSize, weight: .semibold))
                .monospacedDigit()
                .foregroundStyle(CompanionPalette.textPrimary)
                .frame(height: layout.percentLineHeight)
                .contentTransition(.numericText())
        }
        .frame(width: layout.sideBodyDepth, height: layout.cellExtent)
        .opacity(reading.status.isStale ? 0.45 : 1)
        .scaleEffect(isSelected ? 1.025 : 1)
        .contentShape(Rectangle())
        .onHover(perform: onHoverChange)
        .onTapGesture(perform: onActivate)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(reading.id.displayName), Hormuz demo")
        .accessibilityValue(reading.headlineText)
        .accessibilityHint("Click to pin or unpin details")
    }
}
