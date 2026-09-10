import SwiftUI

enum CompanionMotion {
    static let unfold = Animation.spring(response: 0.42, dampingFraction: 0.78)
    static let contents = Animation.spring(response: 0.36, dampingFraction: 0.82)
    static let glide = Animation.spring(response: 0.50, dampingFraction: 0.86)
    static let crossfade = Animation.easeInOut(duration: 0.16)
    static let reading = Animation.spring(response: 0.90, dampingFraction: 0.90)

    static func stagger(index: Int) -> Animation {
        contents.delay(min(Double(index) * 0.045, 0.18))
    }

    static func respectingReduceMotion(_ animation: Animation, reduceMotion: Bool) -> Animation? {
        reduceMotion ? nil : animation
    }
}
