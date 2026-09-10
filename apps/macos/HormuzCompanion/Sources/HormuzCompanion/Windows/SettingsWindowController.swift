import AppKit
import SwiftUI

@MainActor
final class SettingsWindowController: NSWindowController, NSWindowDelegate {
    private let store: CompanionStore
    private var displayOptions: [DisplayOption]
    private let showWidgetAction: () -> Void
    private let quitAction: () -> Void

    init(
        store: CompanionStore,
        displayOptions: [DisplayOption],
        showWidget: @escaping () -> Void,
        quit: @escaping () -> Void
    ) {
        self.store = store
        self.displayOptions = displayOptions
        showWidgetAction = showWidget
        quitAction = quit

        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 440, height: 330),
            styleMask: [.titled, .closable],
            backing: .buffered,
            defer: false
        )
        window.title = "Hormuz Companion Settings"
        window.isReleasedWhenClosed = false
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
        window?.center()
        window?.makeKeyAndOrderFront(nil)
    }

    private func rebuildContent() {
        window?.contentView = NSHostingView(
            rootView: SettingsView(
                store: store,
                displayOptions: displayOptions,
                showWidget: showWidgetAction,
                quit: quitAction
            )
        )
    }
}
