import SwiftUI

struct SettingsHandle: View {
    @ObservedObject var store: CompanionStore
    let openSettings: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var pointerInside = false

    private var layout: CompanionLayout { CompanionLayout(uiScale: store.uiScale) }
    private var hovered: Bool { pointerInside || store.forceSettingsHover }

    var body: some View {
        ZStack {
            Circle()
                .trim(from: 0.75, to: 1)
                .stroke(
                    CompanionPalette.notch,
                    style: StrokeStyle(lineWidth: layout.orbStroke, lineCap: .round)
                )
                .frame(width: layout.orbArcRadius * 2, height: layout.orbArcRadius * 2)
                .opacity(hovered ? 0 : 1)
                .scaleEffect(hovered ? 0.86 : 1)

            Circle()
                .fill(CompanionPalette.notch)
                .frame(width: layout.orbDiameter, height: layout.orbDiameter)
                .opacity(hovered ? 1 : 0)
                .scaleEffect(hovered ? 1 : 1.1)

            Image(systemName: "gearshape")
                .font(.system(size: layout.orbGlyph, weight: .regular))
                .foregroundStyle(CompanionPalette.textPrimary)
                .opacity(hovered ? 1 : 0)
                .scaleEffect(hovered ? 1 : 0.5)
                .rotationEffect(.degrees(hovered ? 0 : -60))
        }
        .frame(width: layout.orbHotZone, height: layout.orbHotZone)
        .contentShape(Circle())
        .onHover { inside in
            pointerInside = inside
            store.settingsHandleHoverChanged(inside)
        }
        .onTapGesture(perform: openSettings)
        .animation(
            CompanionMotion.respectingReduceMotion(
                .spring(response: 0.36, dampingFraction: 0.70),
                reduceMotion: reduceMotion
            ),
            value: hovered
        )
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Hormuz settings")
        .accessibilityHint("Opens demo settings")
    }
}
