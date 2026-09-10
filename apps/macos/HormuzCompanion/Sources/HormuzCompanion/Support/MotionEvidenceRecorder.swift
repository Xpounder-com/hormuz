import AppKit
import Darwin
import HormuzCompanionCore
import ImageIO
import UniformTypeIdentifiers

@MainActor
enum MotionEvidenceRecorder {
    enum RecordingError: LocalizedError {
        case noPanels
        case cannotCreateDestination
        case finalizeFailed
        case publishFailed(Int32)

        var errorDescription: String? {
            switch self {
            case .noPanels: "No visible panels to record"
            case .cannotCreateDestination: "Could not create animated GIF destination"
            case .finalizeFailed: "Could not finalize animated GIF"
            case .publishFailed(let code):
                "Could not publish animated GIF: \(String(cString: strerror(code)))"
            }
        }
    }

    static func record(
        to url: URL,
        store: CompanionStore,
        hover: HoverCoordinator,
        panels: @escaping @MainActor () -> [(name: String, panel: NSPanel)]
    ) async throws {
        let activity = ProcessInfo.processInfo.beginActivity(
            options: [.userInitiated, .latencyCritical],
            reason: "Recording Hormuz Companion visual evidence"
        )
        defer { ProcessInfo.processInfo.endActivity(activity) }

        let fileManager = FileManager.default
        try fileManager.createDirectory(
            at: url.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        let temporaryURL = url.deletingLastPathComponent()
            .appendingPathComponent(".HormuzCompanion-\(UUID().uuidString).gif")
        defer { try? fileManager.removeItem(at: temporaryURL) }

        store.ensureWidgetVisible()
        hover.showAndPin(.budget)
        try await Task.sleep(nanoseconds: 300_000_000)
        let initialPanels = panels()
        guard !initialPanels.isEmpty else { throw RecordingError.noPanels }
        let union = initialPanels.dropFirst().reduce(initialPanels[0].panel.frame) {
            $0.union($1.panel.frame)
        }
        let canvasFrame = union.insetBy(dx: -32, dy: -32)
        let frameCount = 120
        hover.togglePin(.budget)

        guard let destination = CGImageDestinationCreateWithURL(
            temporaryURL as CFURL,
            UTType.gif.identifier as CFString,
            frameCount,
            nil
        ) else {
            throw RecordingError.cannotCreateDestination
        }

        let gifProperties: [CFString: Any] = [
            kCGImagePropertyGIFDictionary: [
                kCGImagePropertyGIFLoopCount: 0
            ]
        ]
        CGImageDestinationSetProperties(destination, gifProperties as CFDictionary)

        let frameProperties: [CFString: Any] = [
            kCGImagePropertyGIFDictionary: [
                kCGImagePropertyGIFDelayTime: 0.1
            ]
        ]

        for frame in 0..<frameCount {
            drive(frame: frame, store: store, hover: hover)
            try await Task.sleep(nanoseconds: 100_000_000)
            let image = try EvidenceCapture.compositeImage(
                panels: panels(),
                canvasFrame: canvasFrame
            )
            let cgImage = try EvidenceCapture.cgImage(from: image)
            CGImageDestinationAddImage(destination, cgImage, frameProperties as CFDictionary)
        }

        guard CGImageDestinationFinalize(destination) else {
            throw RecordingError.finalizeFailed
        }

        let publishResult = temporaryURL.path.withCString { sourcePath in
            url.path.withCString { destinationPath in
                Darwin.rename(sourcePath, destinationPath)
            }
        }
        guard publishResult == 0 else {
            throw RecordingError.publishFailed(errno)
        }
    }

    private static func drive(
        frame: Int,
        store: CompanionStore,
        hover: HoverCoordinator
    ) {
        switch frame {
        case 16:
            hover.leaveMetric(.budget)
        case 17:
            hover.enterTooltip()
        case 25:
            hover.leaveTooltip()
            hover.enterMetric(.tokens)
        case 38:
            hover.enterMetric(.requests)
        case 50:
            hover.togglePin(.requests)
        case 62:
            hover.togglePin(.requests)
            hover.leaveMetric(.requests)
        case 68:
            hover.dismiss()
            store.setTransientVisibilityMode(.fold)
            store.pointerExitedWidget(hover: hover)
        case 84:
            store.pointerEnteredWidget()
        default:
            break
        }
    }
}
