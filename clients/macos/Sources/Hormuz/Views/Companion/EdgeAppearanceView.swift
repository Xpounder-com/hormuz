import HormuzClientCore
import SwiftUI

struct EdgeAppearanceView: View {
    @ObservedObject var presentation: CompanionPresentationModel
    let displays: [DisplayOption]
    let scale: CGFloat

    var body: some View {
        VStack(alignment: .leading, spacing: 22 * scale) {
            section("Widget size", subtitle: "Changes apply immediately.") {
                HStack(spacing: 3 * scale) {
                    ForEach([1.0, 1.25, 1.5], id: \.self) { value in
                        Button { presentation.setUIScale(value) } label: {
                            Text("\(Int(value * 100))%")
                                .font(.system(size: 12 * scale, weight: .medium))
                                .frame(maxWidth: .infinity).padding(.vertical, 7 * scale)
                                .background(presentation.uiScale == value ? CompanionPalette.brandSignal : .clear,
                                            in: RoundedRectangle(cornerRadius: 6 * scale))
                                .foregroundStyle(presentation.uiScale == value ? Color.black : Color.primary)
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("Widget size \(Int(value * 100)) percent")
                        .accessibilityAddTraits(presentation.uiScale == value ? .isSelected : [])
                    }
                }
                .padding(3 * scale)
                .background(.white.opacity(0.07), in: RoundedRectangle(cornerRadius: 9 * scale))
            }
            section("Visibility", subtitle: "The controls stay open while you use them.") {
                Picker("Visibility", selection: Binding(get: { presentation.visibilityMode }, set: presentation.setVisibilityMode)) {
                    ForEach(CompanionVisibilityMode.allCases) { Text($0.displayName).tag($0) }
                }.labelsHidden().pickerStyle(.menu)
            }
            section("Display", subtitle: "Falls back to the primary display if disconnected.") {
                Picker("Display", selection: Binding(get: { presentation.selectedDisplayID }, set: presentation.setSelectedDisplayID)) {
                    Text("Primary display").tag("primary")
                    ForEach(displays) { Text($0.name).tag($0.id) }
                }.labelsHidden().pickerStyle(.menu)
            }
            Button("Hide widget", action: presentation.hideWidget).buttonStyle(.bordered)
            Text("Bring it back from the Hormuz menu-bar icon.")
                .font(.system(size: 11 * scale)).foregroundStyle(.secondary)
        }
    }

    private func section<Content: View>(_ title: String, subtitle: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 9 * scale) {
            Text(title).fontWeight(.medium)
            content()
            Text(subtitle).font(.system(size: 11 * scale)).foregroundStyle(.secondary)
        }
    }
}
