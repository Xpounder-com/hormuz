import SwiftUI

enum CompanionPalette {
    static let notch = Color.black
    static let card = Color.black
    static let brandSignal = Color(hex: 0xDCEDA6)
    static let ringTrack = Color(hex: 0x303030)
    static let barTrack = Color(hex: 0x2D2D2D)
    static let live = Color(hex: 0x00FF88)
    static let watch = Color(hex: 0xF2FF00)
    static let critical = Color(hex: 0xFF3F00)
    static let textPrimary = Color.white
    static let textSecondary = Color(hex: 0x8A8A8A)
}

extension Color {
    init(hex: UInt32) {
        self.init(
            .sRGB,
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255,
            opacity: 1
        )
    }
}
