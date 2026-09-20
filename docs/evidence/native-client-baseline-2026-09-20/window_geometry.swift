// Print numeric geometry for one process's on-screen windows. This does not
// capture pixels, window names, other process identities, or application data.
import Foundation
import CoreGraphics

guard CommandLine.arguments.count == 2,
      let pid = Int32(CommandLine.arguments[1]) else {
    fputs("usage: window_geometry <pid>\n", stderr)
    exit(2)
}

let windows = CGWindowListCopyWindowInfo(
    [.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID
) as? [[String: Any]] ?? []
let geometry: [[String: Any]] = windows.compactMap { window in
    guard (window[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == pid else {
        return nil
    }
    let bounds = window[kCGWindowBounds as String] as? [String: Any] ?? [:]
    return [
        "x": bounds["X"] ?? 0,
        "y": bounds["Y"] ?? 0,
        "width": bounds["Width"] ?? 0,
        "height": bounds["Height"] ?? 0,
        "layer": window[kCGWindowLayer as String] ?? 0,
        "alpha": window[kCGWindowAlpha as String] ?? 0,
    ]
}
let data = try JSONSerialization.data(withJSONObject: geometry, options: [.sortedKeys])
print(String(decoding: data, as: UTF8.self))
