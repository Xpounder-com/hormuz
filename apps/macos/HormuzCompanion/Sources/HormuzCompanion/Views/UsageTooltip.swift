import HormuzCompanionCore
import SwiftUI

private struct TooltipTail: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        path.move(to: CGPoint(x: rect.minX, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.midY))
        path.addLine(to: CGPoint(x: rect.minX, y: rect.maxY))
        path.closeSubpath()
        return path
    }
}

private struct DetailBlockView: View {
    let block: CompanionDetailBlock
    let layout: CompanionLayout

    private var bandColor: Color {
        guard let usedFraction = block.usedFraction else { return .clear }
        return switch CompanionUsageBand.band(for: usedFraction) {
        case .ample: CompanionPalette.ample
        case .watch: CompanionPalette.watch
        case .critical, .exhausted: CompanionPalette.critical
        }
    }

    private var fillFraction: CGFloat {
        CGFloat(min(max(block.usedFraction ?? 0, 0), 1))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 8) {
                Text(block.label)
                    .foregroundStyle(CompanionPalette.textPrimary)
                Spacer(minLength: 4)
                if let trailingText = block.trailingText {
                    Text(trailingText)
                        .foregroundStyle(CompanionPalette.textSecondary)
                }
            }
            .lineLimit(1)

            Group {
                if block.usedFraction != nil {
                    GeometryReader { geometry in
                        ZStack(alignment: .leading) {
                            Capsule().fill(CompanionPalette.barTrack)
                            Capsule()
                                .fill(bandColor)
                                .frame(
                                    width: max(
                                        layout.barHeight,
                                        geometry.size.width * fillFraction
                                    )
                                )
                        }
                    }
                } else {
                    Color.clear
                }
            }
            .frame(height: layout.barHeight)
            .padding(.top, layout.labelToBar)

            Text(block.valueText)
                .foregroundStyle(CompanionPalette.textPrimary)
                .lineLimit(1)
                .padding(.top, layout.barToUsed)
        }
        .font(.system(size: layout.cardBodyFontSize, weight: .regular))
    }
}

struct UsageTooltip: View {
    @ObservedObject var store: CompanionStore
    @ObservedObject var hover: HoverCoordinator
    let openSettings: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var layout: CompanionLayout { CompanionLayout(uiScale: store.uiScale) }
    private var reading: CompanionMetricReading? {
        hover.selectedMetric.flatMap(store.snapshot.metric)
    }

    var body: some View {
        ZStack(alignment: .center) {
            if let reading {
                HStack(spacing: 0) {
                    card(reading)
                    TooltipTail()
                        .fill(CompanionPalette.card)
                        .frame(width: layout.tailLength, height: layout.tailHeight)
                }
                .padding(layout.panelShadowInset)
                .transition(
                    .opacity.combined(
                        with: .scale(scale: 0.97, anchor: .trailing)
                    )
                )
                .onHover { inside in
                    inside ? hover.enterTooltip() : hover.leaveTooltip()
                }
            }
        }
        .frame(
            width: layout.tooltipPanelWidth,
            height: layout.tooltipPanelHeight(for: reading)
        )
        .animation(
            CompanionMotion.respectingReduceMotion(
                CompanionMotion.contents,
                reduceMotion: reduceMotion
            ),
            value: hover.selectedMetric
        )
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Hormuz demo details")
    }

    private func card(_ reading: CompanionMetricReading) -> some View {
        ZStack(alignment: .topLeading) {
            RoundedRectangle(cornerRadius: layout.cardCorner, style: .circular)
                .fill(CompanionPalette.card)

            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: layout.headerGap) {
                    Image(systemName: reading.id.symbolName)
                        .font(.system(size: layout.glyphSize, weight: .medium))
                    Text("\(reading.id.displayName) · Demo")
                        .font(.system(size: layout.cardTitleFontSize, weight: .semibold))
                    Spacer(minLength: 2)
                    if hover.pinnedMetric == reading.id {
                        Image(systemName: "pin.fill")
                            .font(.system(size: layout.cardBodyFontSize))
                            .foregroundStyle(CompanionPalette.textSecondary)
                            .accessibilityLabel("Pinned")
                    }
                }
                .foregroundStyle(CompanionPalette.textPrimary)
                .frame(height: layout.cardTitleLineHeight)

                if let message = reading.message {
                    Text(message)
                        .font(.system(size: layout.cardBodyFontSize, weight: .semibold))
                        .foregroundStyle(messageColor(for: reading.status))
                        .lineLimit(1)
                        .padding(.top, layout.headerToBlock)
                }

                VStack(alignment: .leading, spacing: layout.blockSpacing) {
                    ForEach(Array(reading.blocks.prefix(2))) { block in
                        DetailBlockView(block: block, layout: layout)
                    }

                    if reading.blocks.isEmpty {
                        unavailablePlaceholder
                    }
                }
                .padding(.top, reading.message == nil ? layout.headerToBlock : layout.blockSpacing)

                Spacer(minLength: layout.blockSpacing)

                Rectangle()
                    .fill(CompanionPalette.ringTrack)
                    .frame(height: max(1, layout.uiScale))

                VStack(alignment: .leading, spacing: 2 * layout.uiScale) {
                    Text("\(store.snapshot.actorName) · \(store.snapshot.teamName)")
                        .foregroundStyle(CompanionPalette.textPrimary)
                    Text(store.snapshot.footerLine2)
                        .foregroundStyle(CompanionPalette.textSecondary)
                }
                .font(.system(size: layout.cardBodyFontSize, weight: .regular))
                .padding(.top, layout.blockSpacing)
            }
            .padding(layout.cardPadding)
            .id(reading.id)
            .transition(.opacity)
            .animation(CompanionMotion.crossfade, value: reading.id)
        }
        .frame(
            width: layout.cardWidth,
            height: layout.cardHeight(for: reading),
            alignment: .topLeading
        )
        .clipShape(RoundedRectangle(cornerRadius: layout.cardCorner, style: .circular))
        .shadow(color: .black.opacity(0.25), radius: 12 * layout.uiScale, y: 4 * layout.uiScale)
        .contentShape(RoundedRectangle(cornerRadius: layout.cardCorner, style: .circular))
        .onTapGesture { hover.togglePin(reading.id) }
    }

    private var unavailablePlaceholder: some View {
        VStack(alignment: .leading, spacing: layout.blockSpacing) {
            Text("No percentage is shown without a verified limit.")
                .foregroundStyle(CompanionPalette.textSecondary)
                .fixedSize(horizontal: false, vertical: true)

            if reading?.status == .needsAuth {
                Button("Open Settings", action: openSettings)
                    .buttonStyle(.plain)
                    .foregroundStyle(CompanionPalette.textPrimary)
            }
        }
        .font(.system(size: layout.cardBodyFontSize, weight: .regular))
    }

    private func messageColor(for status: CompanionReadingStatus) -> Color {
        switch status {
        case .offline, .needsAuth, .unsupported: CompanionPalette.critical
        case .stale: CompanionPalette.watch
        case .current: CompanionPalette.textPrimary
        }
    }
}
