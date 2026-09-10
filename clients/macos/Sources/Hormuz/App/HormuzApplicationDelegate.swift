import AppKit
import HormuzClientCore

@MainActor
final class HormuzApplicationDelegate: NSObject, NSApplicationDelegate {
    let connection = ConnectionModel()
    let presentation = CompanionPresentationModel()
    let hover = HoverCoordinator()

    private var edgePanelController: EdgePanelController?
    private var controlCenterController: HormuzWindowController?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)

        let edge = EdgePanelController(
            connection: connection,
            presentation: presentation,
            hover: hover,
            openControlCenter: { [weak self] in self?.showExpandedControlCenter() }
        )
        edgePanelController = edge
        controlCenterController = HormuzWindowController(
            connection: connection,
            presentation: presentation,
            displayOptions: edge.displayOptions,
            showWidget: { [weak self] in self?.showWidget() }
        )
        edge.start()

        if presentation.showControlCenterOnLaunch {
            showControlCenter()
        }

        Task { [weak self] in
            guard let self else { return }
            // Deterministic visual runs must not restore or refresh a real session.
            guard presentation.previewSnapshot == nil else { return }
            await connection.restore()
            if !connection.hasSession {
                edge.showHub(.connection)
            }
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        edgePanelController?.stop()
    }

    func applicationShouldHandleReopen(
        _ sender: NSApplication,
        hasVisibleWindows flag: Bool
    ) -> Bool {
        showControlCenter()
        return true
    }

    func showControlCenter() {
        edgePanelController?.showHub()
    }

    func showExpandedControlCenter() {
        if let edgePanelController {
            controlCenterController?.updateDisplayOptions(edgePanelController.displayOptions)
        }
        controlCenterController?.show()
    }

    func showClientSetup() {
        edgePanelController?.showHub(.client)
    }

    func showWidget() {
        edgePanelController?.showWidget()
    }

    func hideWidget() {
        edgePanelController?.hideWidget()
    }

    func showAndPin(_ metric: CompanionMetricID) {
        edgePanelController?.showAndPin(metric)
    }
}
