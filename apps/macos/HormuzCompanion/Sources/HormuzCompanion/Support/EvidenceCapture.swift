import AppKit

@MainActor
enum EvidenceCapture {
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
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true
        )

        var captures: [(name: String, image: NSImage, frame: NSRect)] = []
        for entry in panels {
            guard let view = entry.panel.contentView else {
                throw CaptureError.missingContent(entry.name)
            }
            let image = try image(of: view, name: entry.name)
            captures.append((entry.name, image, entry.panel.frame))
            try write(
                image,
                to: directory.appendingPathComponent("\(name)-\(entry.name).png")
            )
        }

        guard !captures.isEmpty else { return }
        let combined = composite(from: captures, canvasFrame: nil)
        try write(combined, to: directory.appendingPathComponent("\(name)-composite.png"))
    }

    static func compositeImage(
        panels: [(name: String, panel: NSPanel)],
        canvasFrame: NSRect
    ) throws -> NSImage {
        let captures = try panels.map { entry -> (name: String, image: NSImage, frame: NSRect) in
            guard let view = entry.panel.contentView else {
                throw CaptureError.missingContent(entry.name)
            }
            return (entry.name, try image(of: view, name: entry.name), entry.panel.frame)
        }
        return composite(from: captures, canvasFrame: canvasFrame)
    }

    static func cgImage(from image: NSImage) throws -> CGImage {
        var proposed = NSRect(origin: .zero, size: image.size)
        guard let result = image.cgImage(forProposedRect: &proposed, context: nil, hints: nil) else {
            throw CaptureError.cannotRender("composite")
        }
        return result
    }

    private static func image(of view: NSView, name: String) throws -> NSImage {
        view.needsLayout = true
        view.layoutSubtreeIfNeeded()
        view.needsDisplay = true
        view.displayIfNeeded()
        let bounds = view.bounds
        guard bounds.width > 0, bounds.height > 0,
              let representation = view.bitmapImageRepForCachingDisplay(in: bounds)
        else {
            throw CaptureError.cannotRender(name)
        }
        view.cacheDisplay(in: bounds, to: representation)
        representation.size = bounds.size
        let image = NSImage(size: bounds.size)
        image.addRepresentation(representation)
        return image
    }

    private static func composite(
        from captures: [(name: String, image: NSImage, frame: NSRect)],
        canvasFrame: NSRect?
    ) -> NSImage {
        let union = captures.dropFirst().reduce(captures[0].frame) { partial, item in
            partial.union(item.frame)
        }
        let padding: CGFloat = 32
        let drawingFrame = canvasFrame ?? union.insetBy(dx: -padding, dy: -padding)
        let canvasSize = drawingFrame.size
        let output = NSImage(size: canvasSize)
        output.lockFocus()
        NSColor(calibratedRed: 0.12, green: 0.17, blue: 0.20, alpha: 1).setFill()
        NSRect(origin: .zero, size: canvasSize).fill()

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

    static func write(_ image: NSImage, to url: URL) throws {
        guard let tiff = image.tiffRepresentation,
              let representation = NSBitmapImageRep(data: tiff),
              let data = representation.representation(using: .png, properties: [:])
        else {
            throw CaptureError.cannotEncode(url.lastPathComponent)
        }
        try data.write(to: url, options: .atomic)
    }
}
