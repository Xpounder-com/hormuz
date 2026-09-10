#!/usr/bin/env swift

import AppKit
import Foundation
import ImageIO
import UniformTypeIdentifiers

enum GIFInspectionError: LocalizedError {
    case usage
    case load
    case frame(Int)
    case encode

    var errorDescription: String? {
        switch self {
        case .usage:
            "Usage: inspect-companion-gif.swift <input.gif> <contact-sheet.png>"
        case .load:
            "Could not load the GIF"
        case .frame(let index):
            "Could not decode GIF frame \(index)"
        case .encode:
            "Could not encode the contact sheet"
        }
    }
}

func frameDelay(source: CGImageSource, index: Int) -> Double {
    guard let properties = CGImageSourceCopyPropertiesAtIndex(source, index, nil) as? [CFString: Any],
          let gif = properties[kCGImagePropertyGIFDictionary] as? [CFString: Any]
    else { return 0 }
    return (gif[kCGImagePropertyGIFUnclampedDelayTime] as? Double)
        ?? (gif[kCGImagePropertyGIFDelayTime] as? Double)
        ?? 0
}

do {
    guard CommandLine.arguments.count == 3 else { throw GIFInspectionError.usage }
    let inputURL = URL(fileURLWithPath: CommandLine.arguments[1])
    let outputURL = URL(fileURLWithPath: CommandLine.arguments[2])
    guard let source = CGImageSourceCreateWithURL(inputURL as CFURL, nil) else {
        throw GIFInspectionError.load
    }

    let count = CGImageSourceGetCount(source)
    guard count > 0,
          let first = CGImageSourceCreateImageAtIndex(source, 0, nil)
    else { throw GIFInspectionError.load }

    let requestedSelections: [(Int, String)] = if count >= 200 {
        [
            (0, "Budget hover"),
            (82, "Tokens hover"),
            (103, "Requests pinned"),
            (133, "Folded")
        ]
    } else {
        [
            (0, "Budget hover"),
            (42, "Tokens hover"),
            (56, "Requests pinned"),
            (74, "Folded"),
            (119, "Re-entered")
        ]
    }
    let selections = requestedSelections.filter { $0.0 < count }

    let scale: CGFloat = 0.5
    let tileWidth = CGFloat(first.width) * scale
    let tileHeight = CGFloat(first.height) * scale
    let gap: CGFloat = 18
    let outer: CGFloat = 24
    let labelHeight: CGFloat = 34
    let columns = 2
    let rows = Int(ceil(Double(selections.count) / Double(columns)))
    let canvasSize = NSSize(
        width: outer * 2 + tileWidth * CGFloat(columns) + gap * CGFloat(columns - 1),
        height: outer * 2 + (tileHeight + labelHeight) * CGFloat(rows) + gap * CGFloat(rows - 1)
    )

    let bitmap = NSBitmapImageRep(
        bitmapDataPlanes: nil,
        pixelsWide: Int(canvasSize.width.rounded()),
        pixelsHigh: Int(canvasSize.height.rounded()),
        bitsPerSample: 8,
        samplesPerPixel: 4,
        hasAlpha: true,
        isPlanar: false,
        colorSpaceName: .deviceRGB,
        bytesPerRow: 0,
        bitsPerPixel: 0
    )!
    bitmap.size = canvasSize

    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
    NSColor(calibratedRed: 0.075, green: 0.094, blue: 0.106, alpha: 1).setFill()
    NSBezierPath(rect: NSRect(origin: .zero, size: canvasSize)).fill()

    let labelAttributes: [NSAttributedString.Key: Any] = [
        .font: NSFont.monospacedSystemFont(ofSize: 14, weight: .semibold),
        .foregroundColor: NSColor.white
    ]

    for (position, selection) in selections.enumerated() {
        guard let frame = CGImageSourceCreateImageAtIndex(source, selection.0, nil) else {
            throw GIFInspectionError.frame(selection.0)
        }
        let column = position % columns
        let row = position / columns
        let x = outer + CGFloat(column) * (tileWidth + gap)
        let y = canvasSize.height - outer - CGFloat(row + 1) * (tileHeight + labelHeight) - CGFloat(row) * gap
        let image = NSImage(cgImage: frame, size: NSSize(width: tileWidth, height: tileHeight))
        image.draw(
            in: NSRect(x: x, y: y, width: tileWidth, height: tileHeight),
            from: .zero,
            operation: .copy,
            fraction: 1
        )
        "FRAME \(selection.0) · \(selection.1)".draw(
            at: NSPoint(x: x, y: y + tileHeight + 8),
            withAttributes: labelAttributes
        )
    }
    NSGraphicsContext.restoreGraphicsState()

    try FileManager.default.createDirectory(
        at: outputURL.deletingLastPathComponent(),
        withIntermediateDirectories: true
    )
    guard let png = bitmap.representation(using: .png, properties: [:]) else {
        throw GIFInspectionError.encode
    }
    try png.write(to: outputURL)

    let duration = (0..<count).reduce(0.0) { $0 + frameDelay(source: source, index: $1) }
    print("frames=\(count)")
    print("pixels=\(first.width)x\(first.height)")
    print(String(format: "duration=%.2fs", duration))
    print("contactSheet=\(outputURL.path)")
} catch {
    fputs("\(error.localizedDescription)\n", stderr)
    exit(1)
}
