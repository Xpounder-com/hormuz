import AppKit
import HormuzCompanionCore
import SwiftUI

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    let store = CompanionStore()
    let hover = HoverCoordinator()
    private var panelController: EdgePanelController?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        let controller = EdgePanelController(store: store, hover: hover)
        panelController = controller
        controller.start()
    }

    func applicationWillTerminate(_ notification: Notification) {
        panelController?.stop()
    }

    func showWidget() { panelController?.showWidget() }
    func hideWidget() { panelController?.hideWidget() }
    func showSettings() { panelController?.showSettings() }
    func showAndPin(_ metric: CompanionMetricID) { panelController?.showAndPin(metric) }
}

@main
struct HormuzCompanionApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    var body: some Scene {
        MenuBarExtra("Hormuz Demo") {
            CompanionMenu(
                store: appDelegate.store,
                hover: appDelegate.hover,
                showWidget: appDelegate.showWidget,
                hideWidget: appDelegate.hideWidget,
                showSettings: appDelegate.showSettings,
                showAndPin: appDelegate.showAndPin
            )
        }
        .menuBarExtraStyle(.menu)
    }
}

private struct CompanionMenu: View {
    @ObservedObject var store: CompanionStore
    @ObservedObject var hover: HoverCoordinator

    let showWidget: () -> Void
    let hideWidget: () -> Void
    let showSettings: () -> Void
    let showAndPin: (CompanionMetricID) -> Void

    var body: some View {
        Text("Hormuz Demo")
        Text("Synthetic data · no gateway")

        Divider()

        if store.widgetVisible {
            Button("Hide Widget", action: hideWidget)
        } else {
            Button("Show Widget", action: showWidget)
        }

        Menu("Show Details") {
            ForEach(CompanionMetricID.allCases) { metric in
                Button(metric.displayName) { showAndPin(metric) }
            }
        }

        Button("Refresh Demo") { store.refreshDemo() }
        Button("Dismiss Details") { hover.dismiss() }
            .disabled(hover.selectedMetric == nil)
            .keyboardShortcut(.escape, modifiers: [])

        Divider()

        Button("Settings…", action: showSettings)
            .keyboardShortcut(",", modifiers: .command)

        Button("Quit Hormuz Demo") {
            NSApplication.shared.terminate(nil)
        }
        .keyboardShortcut("q", modifiers: .command)
    }
}
