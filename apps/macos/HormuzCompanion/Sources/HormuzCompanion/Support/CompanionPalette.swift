import SwiftUI

enum CompanionPalette {
    static let notch = Color.black
    static let card = Color.black
    static let ringTrack = Color(hex: 0x303030)
    static let barTrack = Color(hex: 0x2D2D2D)
    static let ample = Color(hex: 0x00FF88)
    static let watch = Color(hex: 0xF2FF00)
    static let critical = Color(hex: 0xFF3F00)
    static let textPrimary = Color.white
    static let textSecondary = Color(hex: 0x808080)
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
