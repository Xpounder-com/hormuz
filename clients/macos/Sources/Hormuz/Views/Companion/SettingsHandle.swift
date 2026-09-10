import SwiftUI

struct SettingsHandle: View {
    @ObservedObject var presentation: CompanionPresentationModel
    let openControlCenter: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var pointerInside = false

    private var layout: CompanionLayout { CompanionLayout(uiScale: presentation.uiScale) }

    var body: some View {
        Button(action: openControlCenter) {
            ZStack {
                Circle()
                    .fill(CompanionPalette.notch)
                    .frame(width: layout.orbDiameter, height: layout.orbDiameter)
                    .overlay(Circle().strokeBorder(.white.opacity(pointerInside ? 0.25 : 0.12)))

                Image(systemName: "gearshape")
                    .font(.system(size: layout.orbGlyph, weight: .regular))
                    .foregroundStyle(CompanionPalette.textPrimary)
                    .opacity(pointerInside ? 1 : 0.75)
                    .rotationEffect(.degrees(pointerInside ? 20 : 0))
            }
            // Animate only the artwork; the surrounding approach/click area
            // stays full size so the small gear is easy to discover and reach.
            .scaleEffect(pointerInside ? 1 : 0.58)
            .frame(width: layout.orbHotZone, height: layout.orbHotZone)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { inside in
            pointerInside = inside
            presentation.settingsHandleHoverChanged(inside)
        }
        .animation(
            CompanionMotion.respectingReduceMotion(
                .easeInOut(duration: pointerInside ? 0.28 : 0.36),
                reduceMotion: reduceMotion
            ),
            value: pointerInside
        )
        .help("Hormuz controls")
        .accessibilityLabel("Hormuz controls")
        .accessibilityHint("Opens connection, client setup, and appearance beside the widget")
    }
}
