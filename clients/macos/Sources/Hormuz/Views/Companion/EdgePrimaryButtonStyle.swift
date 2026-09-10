import SwiftUI

/// Keeps a readable fill even when a nonactivating panel is not the key window.
struct EdgePrimaryButtonStyle: ButtonStyle {
    let scale: CGFloat
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .fontWeight(.medium)
            .padding(.horizontal, 13 * scale)
            .padding(.vertical, 8 * scale)
            .foregroundStyle(isEnabled ? Color.black : Color.white.opacity(0.55))
            .background(isEnabled ? CompanionPalette.brandSignal.opacity(configuration.isPressed ? 0.8 : 1)
                        : Color.white.opacity(0.08), in: RoundedRectangle(cornerRadius: 8 * scale))
            .contentShape(Rectangle())
    }
}
