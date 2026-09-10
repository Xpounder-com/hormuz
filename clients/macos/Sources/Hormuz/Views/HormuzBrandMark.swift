import AppKit
import SwiftUI

/// Uses the approved website artwork, copied into both local and release bundles.
struct HormuzBrandMark: View {
    private static let artwork = load("HormuzMark")
    static let menuImage: NSImage = {
        let image = load("HormuzMenuMark")
        image.size = NSSize(width: 18, height: 18)
        image.isTemplate = true
        return image
    }()

    var body: some View {
        Image(nsImage: Self.artwork)
            .renderingMode(.template)
            .resizable()
            .scaledToFit()
            .accessibilityHidden(true)
    }

    private static func load(_ name: String) -> NSImage {
        guard let url = Bundle.main.url(forResource: name, withExtension: "png"),
              let image = NSImage(contentsOf: url) else {
            assertionFailure("Missing bundled Hormuz brand asset: \(name)")
            return NSImage(size: NSSize(width: 18, height: 18))
        }
        return image
    }
}
