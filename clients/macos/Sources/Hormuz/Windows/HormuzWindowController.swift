import AppKit
import SwiftUI

@MainActor
final class HormuzWindowController: NSWindowController, NSWindowDelegate {
    private let connection: ConnectionModel
    private let presentation: CompanionPresentationModel
    private var displayOptions: [DisplayOption]
    private let showWidgetAction: () -> Void

    init(
        connection: ConnectionModel,
        presentation: CompanionPresentationModel,
        displayOptions: [DisplayOption],
        showWidget: @escaping () -> Void
    ) {
        self.connection = connection
        self.presentation = presentation
        self.displayOptions = displayOptions
        showWidgetAction = showWidget

        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 720, height: 760),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "Hormuz"
        window.minSize = NSSize(width: 650, height: 650)
        window.isReleasedWhenClosed = false
        window.setFrameAutosaveName("HormuzControlCenter")
        window.center()
        super.init(window: window)
        window.delegate = self
        rebuildContent()
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    func updateDisplayOptions(_ options: [DisplayOption]) {
        displayOptions = options
        rebuildContent()
    }

    func show() {
        rebuildContent()
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
    }

    private func rebuildContent() {
        window?.contentView = NSHostingView(
            rootView: ControlCenterView(
                connection: connection,
                presentation: presentation,
                displayOptions: displayOptions,
                showWidget: showWidgetAction
            )
        )
    }
}
