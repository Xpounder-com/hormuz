import HormuzClientCore
import SwiftUI

struct EdgeNotchView: View {
    @Bindable var connection: ConnectionModel
    @ObservedObject var presentation: CompanionPresentationModel
    @ObservedObject var hover: HoverCoordinator
    @ObservedObject var surface: EdgeSurfaceState

    let openControlCenter: () -> Void
    let hideWidget: () -> Void
    let quit: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var snapshot: CompanionSnapshot { presentation.snapshot(for: connection) }
    private var layout: CompanionLayout { CompanionLayout(uiScale: presentation.uiScale) }
    private var expanded: Bool { surface.expanded }

    var body: some View {
        Group {
            if expanded { expandedBody } else { foldedBody }
        }
        .frame(
            width: expanded ? layout.sideBodyDepth : layout.pillHotZone,
            height: expanded ? layout.shapeHeight(metrics: snapshot.metrics) : layout.pillHeight,
            alignment: .trailing
        )
        .onHover { inside in
            inside
                ? presentation.pointerEnteredWidget()
                : presentation.pointerExitedWidget(hover: hover)
        }
        .contextMenu {
            Button("Open Hormuz", action: openControlCenter)
            Button("Refresh status") { connection.refresh() }
                .disabled(!connection.hasSession || connection.isBusy)
            Divider()
            Button("Hide widget", action: hideWidget)
            Button("Quit Hormuz", action: quit)
        }
    }

    private var expandedBody: some View {
        ZStack(alignment: .topTrailing) {
            SideNotchShape(curlRadius: layout.curlRadius, cornerRadius: layout.cornerRadius)
                .fill(CompanionPalette.notch)

            VStack(spacing: layout.cellSpacing) {
                ForEach(Array(snapshot.metrics.enumerated()), id: \.element.id) { index, reading in
                    UsageRingCell(
                        reading: reading,
                        layout: layout,
                        isSelected: hover.selectedMetric == reading.id,
                        onHoverChange: { inside in
                            if inside {
                                presentation.pointerEnteredWidget()
                                if !presentation.hub.isOpen { hover.enterMetric(reading.id) }
                            } else {
                                hover.leaveMetric(reading.id)
                            }
                        },
                        onActivate: {
                            presentation.hub.close()
                            presentation.ensureWidgetVisible()
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
        .frame(width: layout.sideBodyDepth, height: layout.shapeHeight(metrics: snapshot.metrics))
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Hormuz usage")
    }

    private var foldedBody: some View {
        Button(action: revealFoldedWidget) {
            HStack(spacing: 0) {
                Spacer(minLength: 0)
                SideNotchShape(curlRadius: layout.curlRadius, cornerRadius: layout.cornerRadius)
                    .fill(CompanionPalette.notch)
                    .frame(width: layout.pillWidth, height: layout.pillHeight)
            }
            .frame(width: layout.pillHotZone, height: layout.pillHeight)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Show Hormuz usage")
    }

    private func revealFoldedWidget() {
        presentation.ensureWidgetVisible()
        openControlCenter()
    }
}
