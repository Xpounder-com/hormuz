import AppKit

@MainActor
enum PanelEvidenceCapture {
    enum CaptureError: LocalizedError {
        case missingContent(String)
        case cannotRender(String)
        case cannotEncode(String)

        var errorDescription: String? {
            switch self {
            case .missingContent(let name): "Missing content view for \(name)"
            case .cannotRender(let name): "Could not render \(name)"
            case .cannotEncode(let name): "Could not encode \(name) as PNG"
            }
        }
    }

    static func capture(
        panels: [(name: String, panel: NSPanel)],
        directory: URL,
        name: String
    ) throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)

        var captures: [(name: String, image: NSImage, frame: NSRect)] = []
        for entry in panels {
            guard let view = entry.panel.contentView else {
                throw CaptureError.missingContent(entry.name)
            }
            let image = try image(of: view, name: entry.name)
            captures.append((entry.name, image, entry.panel.frame))
            try write(image, to: directory.appendingPathComponent("\(name)-\(entry.name).png"))
        }

        guard !captures.isEmpty else { return }
        try write(composite(from: captures), to: directory.appendingPathComponent("\(name)-composite.png"))
    }

    private static func image(of view: NSView, name: String) throws -> NSImage {
        view.needsLayout = true
        view.layoutSubtreeIfNeeded()
        view.needsDisplay = true
        view.displayIfNeeded()
        let bounds = view.bounds
        guard bounds.width > 0,
              bounds.height > 0,
              let representation = view.bitmapImageRepForCachingDisplay(in: bounds) else {
            throw CaptureError.cannotRender(name)
        }
        view.cacheDisplay(in: bounds, to: representation)
        representation.size = bounds.size
        let image = NSImage(size: bounds.size)
        image.addRepresentation(representation)
        return image
    }

    private static func composite(
        from captures: [(name: String, image: NSImage, frame: NSRect)]
    ) -> NSImage {
        let union = captures.dropFirst().reduce(captures[0].frame) { partial, item in
            partial.union(item.frame)
        }
        let padding: CGFloat = 32
        let drawingFrame = union.insetBy(dx: -padding, dy: -padding)
        let output = NSImage(size: drawingFrame.size)
        output.lockFocus()
        NSColor(calibratedRed: 0.12, green: 0.17, blue: 0.20, alpha: 1).setFill()
        NSRect(origin: .zero, size: drawingFrame.size).fill()

        for capture in captures {
            let destination = NSRect(
                x: capture.frame.minX - drawingFrame.minX,
                y: capture.frame.minY - drawingFrame.minY,
                width: capture.frame.width,
                height: capture.frame.height
            )
            capture.image.draw(in: destination)
        }
        output.unlockFocus()
        return output
    }

    private static func write(_ image: NSImage, to url: URL) throws {
        guard let tiff = image.tiffRepresentation,
              let representation = NSBitmapImageRep(data: tiff),
              let data = representation.representation(using: .png, properties: [:]) else {
            throw CaptureError.cannotEncode(url.lastPathComponent)
        }
        try data.write(to: url, options: .atomic)
    }
}
