import AppKit
import CoreGraphics
import HormuzClientCore
import Network

@MainActor
final class HormuzApplicationDelegate: NSObject, NSApplicationDelegate {
    let connection = ConnectionModel()
    let presentation = CompanionPresentationModel()
    let hover = HoverCoordinator()

    private var edgePanelController: EdgePanelController?
    private var controlCenterController: HormuzWindowController?
    private var workspaceObservers: [NSObjectProtocol] = []
    private var lockObservers: [NSObjectProtocol] = []
    private let networkMonitor = NWPathMonitor()
    private var terminationTask: Task<Void, Never>?

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
        // Public console/login fields identify session switching. The optional
        // screen-lock field and distributed notifications are advisory macOS
        // implementation details, not a documented native qualification proof.
        // Missing/unknown initial state blocks polling until an OS resume input.
        refreshNativeSessionGate()
        let center = NSWorkspace.shared.notificationCenter
        for (name, locked, asleep) in [
            (NSWorkspace.sessionDidResignActiveNotification, true as Bool?, nil as Bool?),
            (NSWorkspace.willSleepNotification, nil as Bool?, true as Bool?),
            (NSWorkspace.didWakeNotification, nil as Bool?, false as Bool?),
            (NSWorkspace.screensDidSleepNotification, nil as Bool?, true as Bool?),
            (NSWorkspace.screensDidWakeNotification, nil as Bool?, false as Bool?),
        ] {
            workspaceObservers.append(center.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                Task { @MainActor in self?.connection.systemLifecycle(locked: locked, asleep: asleep) }
            })
        }
        workspaceObservers.append(center.addObserver(forName: NSWorkspace.sessionDidBecomeActiveNotification,
            object: nil, queue: .main) { [weak self] _ in
                Task { @MainActor in self?.refreshNativeSessionGate() }
            })
        let distributed = DistributedNotificationCenter.default()
        for (name, locked) in [("com.apple.screenIsLocked", true), ("com.apple.screenIsUnlocked", false)] {
            lockObservers.append(distributed.addObserver(forName: Notification.Name(name), object: nil,
                queue: .main) { [weak self] _ in
                    Task { @MainActor in self?.refreshNativeSessionGate(screenLocked: locked) }
                })
        }
        networkMonitor.pathUpdateHandler = { [weak self] _ in
            Task { @MainActor in self?.connection.systemLifecycle(networkChanged: true) }
        }
        networkMonitor.start(queue: DispatchQueue(label: "com.hormuz.network-events"))

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

    private func refreshNativeSessionGate(screenLocked: Bool? = nil) {
        let session = CGSessionCopyCurrentDictionary() as? [String: Any]
        connection.systemLifecycle(locked: NativeSessionGate.isBlocked(
            onConsole: session?[kCGSessionOnConsoleKey as String] as? Bool,
            loginDone: session?[kCGSessionLoginDoneKey as String] as? Bool,
            screenLocked: screenLocked ?? (session?["CGSSessionScreenIsLocked"] as? Bool)))
    }

    func applicationWillTerminate(_ notification: Notification) {
        connection.stopLaunchedClients()
        edgePanelController?.stop()
        for observer in workspaceObservers { NSWorkspace.shared.notificationCenter.removeObserver(observer) }
        workspaceObservers.removeAll()
        for observer in lockObservers { DistributedNotificationCenter.default().removeObserver(observer) }
        lockObservers.removeAll()
        networkMonitor.cancel()
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard terminationTask == nil else { return .terminateLater }
        let active = connection.beginClientDrain()
        if active > 0 {
            let alert = NSAlert()
            alert.messageText = "\(active) launched client\(active == 1 ? " is" : "s are") still active"
            alert.informativeText = "Closing or hiding Hormuz controls keeps these clients running. Wait for them to exit, cancel quitting, or explicitly stop their governed relay and quit. A stopped request is never automatically replayed."
            alert.addButton(withTitle: "Wait for clients")
            alert.addButton(withTitle: "Cancel")
            alert.addButton(withTitle: "Stop clients and quit")
            switch alert.runModal() {
            case .alertFirstButtonReturn:
                connection.waitForClientsBeforeQuit { NSApp.terminate(nil) }
                return .terminateCancel
            case .alertSecondButtonReturn:
                connection.cancelPendingQuit()
                return .terminateCancel
            default: break // Explicit user-confirmed forced quit, never an update.
            }
        }
        terminationTask = Task { [weak self] in
            guard let self else { sender.reply(toApplicationShouldTerminate: false); return }
            await connection.shutdown()
            edgePanelController?.stop()
            sender.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
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
