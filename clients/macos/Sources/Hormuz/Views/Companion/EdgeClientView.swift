import HormuzClientCore
import SwiftUI

struct EdgeClientView: View {
    @Bindable var connection: ConnectionModel
    @ObservedObject var navigation: EdgeHubNavigation
    let scale: CGFloat
    let showReview: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 16 * scale) {
            if showReview, let connector = connection.connector {
                Text("Review the Hormuz launcher before saving. Your existing client settings stay unchanged.")
                    .foregroundStyle(.secondary)
                ScrollView(.horizontal) {
                    Text(connector.previewText)
                        .font(.system(size: 11 * scale, design: .monospaced))
                        .textSelection(.enabled).padding(12 * scale)
                }.background(.white.opacity(0.06), in: RoundedRectangle(cornerRadius: 10 * scale))
                Text("Run the launcher from your project directory. Moving Hormuz requires saving a new connector.")
                    .foregroundStyle(.secondary).font(.system(size: 11 * scale))
                Button("Save connector", action: connection.saveConnector)
                    .buttonStyle(EdgePrimaryButtonStyle(scale: scale))
                    .disabled(connection.isBusy)
                Button("Cancel review") { navigation.back() }.disabled(connection.isBusy)
            } else if let profile = connection.profile, connection.hasSession {
                Label(profile.client.title, systemImage: "terminal")
                    .font(.system(size: 20 * scale, weight: .semibold))
                Text(profile.model).textSelection(.enabled)
                Text("Hormuz settings apply when you use its dedicated launcher from your project directory.")
                    .foregroundStyle(.secondary)
                Toggle(
                    "Context optimization",
                    isOn: Binding(
                        get: { connection.contextOptimizationEnabled },
                        set: { connection.setContextOptimization(enabled: $0) }
                    )
                )
                .disabled(connection.isBusy || connection.sessionState != .active)
                Text(connection.contextOptimizationStatusLabel)
                    .font(.system(size: 11 * scale))
                    .foregroundStyle(connection.contextOptimizationStatus == .settingsInvalid ? Color.orange : Color.secondary)
                Text("Runs on this Mac before the request reaches Hormuz. Only eligible tool results change; new chats have the most stable cache behavior.")
                    .font(.system(size: 11 * scale)).foregroundStyle(.secondary)
                Button(connection.connectorSaved ? "Review client setup" : "Set up client") {
                    if connection.previewConnector(presentSheet: false) { navigation.open(.review) }
                }
                .buttonStyle(EdgePrimaryButtonStyle(scale: scale))
                .disabled(connection.isBusy || connection.sessionState != .active
                          || (connection.expiresAt.map { $0 <= Date() } ?? false))
                if connection.connectorSaved {
                    Button("Copy launch command", action: connection.copyCommand)
                        .disabled(connection.isBusy)
                }
                Text("To change the client or model, sign out and reconnect with your new selection.")
                    .font(.system(size: 11 * scale)).foregroundStyle(.secondary)
                Text("Connectors saved before context optimization must be saved again once.")
                    .font(.system(size: 11 * scale)).foregroundStyle(.secondary)
                Button("Manage connection") { navigation.open(.connection) }
            } else {
                Label("Bring your tools", systemImage: "terminal")
                    .font(.system(size: 20 * scale, weight: .semibold))
                Text("Connect to your team first, then set up a governed Codex or Claude Code launcher.")
                    .foregroundStyle(.secondary)
                Button("Connect to Hormuz") { navigation.open(.setup) }
                    .buttonStyle(EdgePrimaryButtonStyle(scale: scale))
            }
        }
        .buttonStyle(.bordered)
        .onChange(of: connection.isBusy) { wasBusy, busy in
            if wasBusy && !busy && showReview && connection.connectorSaved { navigation.open(.client) }
        }
    }
}
