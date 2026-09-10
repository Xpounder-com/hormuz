import HormuzCompanionCore
import SwiftUI

struct EdgeNotchView: View {
    @ObservedObject var store: CompanionStore
    @ObservedObject var hover: HoverCoordinator

    let openSettings: () -> Void
    let refreshDemo: () -> Void
    let hideWidget: () -> Void
    let quit: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var layout: CompanionLayout { CompanionLayout(uiScale: store.uiScale) }
    private var expanded: Bool { store.shouldExpand(hover: hover) }

    var body: some View {
        Group {
            if expanded {
                expandedBody
            } else {
                foldedBody
            }
        }
        .frame(
            width: expanded ? layout.sideBodyDepth : layout.pillHotZone,
            height: expanded ? layout.shapeHeight(metricCount: store.snapshot.metrics.count) : layout.pillHeight,
            alignment: .trailing
        )
        .onHover { inside in
            inside ? store.pointerEnteredWidget() : store.pointerExitedWidget(hover: hover)
        }
        .contextMenu {
            Button("Settings…", action: openSettings)
            Button("Refresh demo", action: refreshDemo)
            Divider()
            Button("Hide widget", action: hideWidget)
            Button("Quit Hormuz Demo", action: quit)
        }
    }

    private var expandedBody: some View {
        ZStack(alignment: .topTrailing) {
            SideNotchShape(
                curlRadius: layout.curlRadius,
                cornerRadius: layout.cornerRadius
            )
            .fill(CompanionPalette.notch)

            VStack(spacing: layout.cellSpacing) {
                ForEach(Array(store.snapshot.metrics.enumerated()), id: \.element.id) { index, reading in
                    UsageRingCell(
                        reading: reading,
                        layout: layout,
                        isSelected: hover.selectedMetric == reading.id,
                        onHoverChange: { inside in
                            if inside {
                                store.pointerEnteredWidget()
                                hover.enterMetric(reading.id)
                            } else {
                                hover.leaveMetric(reading.id)
                            }
                        },
                        onActivate: {
                            store.ensureWidgetVisible()
                            hover.togglePin(reading.id)
                        }
                    )
                    .opacity(expanded ? 1 : 0)
                    .scaleEffect(expanded ? 1 : 0.88, anchor: .trailing)
                    .animation(
                        CompanionMotion.respectingReduceMotion(
                            CompanionMotion.stagger(index: index),
                            reduceMotion: reduceMotion
                        ),
                        value: expanded
                    )
                }
            }
            .padding(.top, layout.curlRadius + layout.padTop)
        }
        .frame(
            width: layout.sideBodyDepth,
            height: layout.shapeHeight(metricCount: store.snapshot.metrics.count)
        )
        .transition(.identity)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Hormuz usage demo")
    }

    private var foldedBody: some View {
        HStack(spacing: 0) {
            Spacer(minLength: 0)
            SideNotchShape(
                curlRadius: layout.curlRadius,
                cornerRadius: layout.cornerRadius
            )
            .fill(CompanionPalette.notch)
            .frame(width: layout.pillWidth, height: layout.pillHeight)
        }
        .frame(width: layout.pillHotZone, height: layout.pillHeight)
        .contentShape(Rectangle())
        .onTapGesture { store.pointerEnteredWidget() }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Show Hormuz usage demo")
    }
}
