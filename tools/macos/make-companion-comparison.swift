#!/usr/bin/env swift

import AppKit
import Foundation

struct Crop {
    let x: CGFloat
    let y: CGFloat
    let width: CGFloat
    let height: CGFloat
    let scale: CGFloat
}

enum ComparisonError: LocalizedError {
    case usage
    case imageLoad(String)
    case crop(String)
    case encode(String)

    var errorDescription: String? {
        switch self {
        case .usage:
            "Usage: make-companion-comparison.swift <reference.png> <implementation.png> <output-directory>"
        case .imageLoad(let path):
            "Could not load image at \(path)"
        case .crop(let path):
            "Could not crop image at \(path)"
        case .encode(let path):
            "Could not encode comparison at \(path)"
        }
    }
}

func loadCGImage(at path: String) throws -> CGImage {
    guard let image = NSImage(contentsOfFile: path),
          let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil)
    else {
        throw ComparisonError.imageLoad(path)
    }
    return cgImage
}

func croppedImage(_ image: CGImage, crop: Crop, sourcePath: String) throws -> NSImage {
    // CGImage cropping uses the source image's top-left pixel coordinates.
    let rect = CGRect(
        x: crop.x,
        y: crop.y,
        width: crop.width,
        height: crop.height
    )
    guard let cropped = image.cropping(to: rect) else {
        throw ComparisonError.crop(sourcePath)
    }
    return NSImage(
        cgImage: cropped,
        size: NSSize(width: crop.width * crop.scale, height: crop.height * crop.scale)
    )
}

func writeComparison(
    reference: CGImage,
    implementation: CGImage,
    referencePath: String,
    implementationPath: String,
    referenceCrop: Crop,
    implementationCrop: Crop,
    outputPath: String
) throws {
    let leftImage = try croppedImage(reference, crop: referenceCrop, sourcePath: referencePath)
    let rightImage = try croppedImage(implementation, crop: implementationCrop, sourcePath: implementationPath)
    let gap: CGFloat = 28
    let outer: CGFloat = 28
    let labelHeight: CGFloat = 56
    let imageHeight = max(leftImage.size.height, rightImage.size.height)
    let canvasSize = NSSize(
        width: outer * 2 + leftImage.size.width + gap + rightImage.size.width,
        height: outer * 2 + labelHeight + imageHeight
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

    let titleAttributes: [NSAttributedString.Key: Any] = [
        .font: NSFont.systemFont(ofSize: 18, weight: .semibold),
        .foregroundColor: NSColor.white
    ]
    let subtitleAttributes: [NSAttributedString.Key: Any] = [
        .font: NSFont.systemFont(ofSize: 13, weight: .regular),
        .foregroundColor: NSColor(calibratedWhite: 0.68, alpha: 1)
    ]

    let leftX = outer
    let rightX = outer + leftImage.size.width + gap
    let imageY = outer
    let labelY = imageY + imageHeight + 10

    "REFERENCE · CODENOTCH".draw(
        at: NSPoint(x: leftX, y: labelY + 19),
        withAttributes: titleAttributes
    )
    "Pinned source frame, normalized to ring diameter".draw(
        at: NSPoint(x: leftX, y: labelY),
        withAttributes: subtitleAttributes
    )
    "IMPLEMENTATION · HORMUZ".draw(
        at: NSPoint(x: rightX, y: labelY + 19),
        withAttributes: titleAttributes
    )
    "Live native SwiftUI/AppKit panel capture".draw(
        at: NSPoint(x: rightX, y: labelY),
        withAttributes: subtitleAttributes
    )

    leftImage.draw(
        in: NSRect(x: leftX, y: imageY + imageHeight - leftImage.size.height, width: leftImage.size.width, height: leftImage.size.height),
        from: .zero,
        operation: .copy,
        fraction: 1
    )
    rightImage.draw(
        in: NSRect(x: rightX, y: imageY + imageHeight - rightImage.size.height, width: rightImage.size.width, height: rightImage.size.height),
        from: .zero,
        operation: .copy,
        fraction: 1
    )
    NSGraphicsContext.restoreGraphicsState()

    let outputURL = URL(fileURLWithPath: outputPath)
    try FileManager.default.createDirectory(
        at: outputURL.deletingLastPathComponent(),
        withIntermediateDirectories: true
    )
    guard let data = bitmap.representation(using: .png, properties: [:]) else {
        throw ComparisonError.encode(outputPath)
    }
    try data.write(to: outputURL)
}

do {
    guard (4...5).contains(CommandLine.arguments.count) else { throw ComparisonError.usage }
    let referencePath = CommandLine.arguments[1]
    let implementationPath = CommandLine.arguments[2]
    let outputDirectory = CommandLine.arguments[3]
    let reference = try loadCGImage(at: referencePath)
    let implementation = try loadCGImage(at: implementationPath)
    let showFullImplementation = CommandLine.arguments.last == "--full"

    try writeComparison(
        reference: reference,
        implementation: implementation,
        referencePath: referencePath,
        implementationPath: implementationPath,
        referenceCrop: Crop(x: 190, y: 430, width: 1_020, height: 1_250, scale: 0.752),
        implementationCrop: showFullImplementation
            ? Crop(x: 0, y: 0, width: CGFloat(implementation.width), height: CGFloat(implementation.height), scale: 1)
            : Crop(x: 50, y: 50, width: 740, height: 900, scale: 1),
        outputPath: "\(outputDirectory)/reference-vs-hormuz-overall.png"
    )
    try writeComparison(
        reference: reference,
        implementation: implementation,
        referencePath: referencePath,
        implementationPath: implementationPath,
        referenceCrop: Crop(x: 190, y: 450, width: 1_020, height: 560, scale: 0.752),
        implementationCrop: Crop(x: 50, y: 50, width: 740, height: 430, scale: 1),
        outputPath: "\(outputDirectory)/reference-vs-hormuz-detail.png"
    )
    print("Wrote normalized overall and detail comparisons to \(outputDirectory)")
} catch {
    fputs("\(error.localizedDescription)\n", stderr)
    exit(1)
}
