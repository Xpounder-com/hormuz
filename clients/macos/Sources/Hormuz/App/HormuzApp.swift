import HormuzClientCore
import SwiftUI

struct HormuzApp: App {
    @NSApplicationDelegateAdaptor(HormuzApplicationDelegate.self) private var appDelegate

    var body: some Scene {
        MenuBarExtra {
            HormuzMenu(
                connection: appDelegate.connection,
                presentation: appDelegate.presentation,
                hover: appDelegate.hover,
                openControlCenter: appDelegate.showControlCenter,
                showWidget: appDelegate.showWidget,
                hideWidget: appDelegate.hideWidget,
                showAndPin: appDelegate.showAndPin,
                showClientSetup: appDelegate.showClientSetup
            )
        } label: {
            Image(nsImage: HormuzBrandMark.menuImage).accessibilityLabel("Hormuz")
        }
        .menuBarExtraStyle(.menu)
        .commands {
            CommandGroup(after: .appInfo) {
                Button("Open Hormuz controls", action: appDelegate.showControlCenter)
                    .keyboardShortcut("o", modifiers: .command)
                Button("Show widget", action: appDelegate.showWidget)
                Divider()
            }
        }
    }
}

private struct HormuzMenu: View {
    @Bindable var connection: ConnectionModel
    @ObservedObject var presentation: CompanionPresentationModel
    @ObservedObject var hover: HoverCoordinator

    let openControlCenter: () -> Void
    let showWidget: () -> Void
    let hideWidget: () -> Void
    let showAndPin: (CompanionMetricID) -> Void
    let showClientSetup: () -> Void

    private var snapshot: CompanionSnapshot { presentation.snapshot(for: connection) }

    var body: some View {
        Text(snapshot.statusLabel)
        if connection.dashboard != nil {
            Text("\(snapshot.actorName) · \(snapshot.teamName)")
        }

        Divider()

        Button("Open Hormuz", action: openControlCenter)

        if presentation.widgetVisible {
            Button("Hide Widget", action: hideWidget)
        } else {
            Button("Show Widget", action: showWidget)
        }

        Menu("Show Usage") {
            ForEach(CompanionMetricID.allCases) { metric in
                Button(metric.displayName) { showAndPin(metric) }
            }
        }

        Button("Refresh Status") { connection.refresh() }
            .disabled(!connection.hasSession || connection.isBusy)
            .keyboardShortcut("r", modifiers: .command)

        Divider()

        if connection.hasSession {
            Button(connection.connectorSaved ? "Review Client Setup…" : "Set Up Client…") {
                showClientSetup()
            }
            .disabled(connection.isBusy || connection.sessionState != .active)

            Button(connection.sessionState == .revocationPending ? "Retry Sign Out" : "Sign Out") {
                connection.signOut()
            }
            .disabled(connection.isBusy)
        } else {
            Button("Sign In…", action: openControlCenter)
        }

        Button("Dismiss Details") { hover.dismiss(); presentation.hub.close() }
            .disabled(hover.selectedMetric == nil && !presentation.hub.isOpen)
            .keyboardShortcut(.escape, modifiers: [])

        Divider()

        Button("Quit Hormuz") { NSApplication.shared.terminate(nil) }
            .keyboardShortcut("q", modifiers: .command)
    }
}
