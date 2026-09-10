import HormuzClientCore
import SwiftUI

struct ConnectedView: View {
    @Bindable var connection: ConnectionModel

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            if let profile = connection.profile {
                Grid(alignment: .leading, horizontalSpacing: 22, verticalSpacing: 9) {
                    row(
                        "Setup",
                        profile.setup == .openAIPilot
                            ? "Hormuz hosted pilot · Codex/OpenAI"
                            : "Custom team gateway"
                    )
                    row("Gateway", profile.gateway)
                    row("Organization", profile.organization)
                    row("Client", profile.client.title + " · " + profile.model)
                    if let identity = connection.dashboard?.identity {
                        row("Signed in as", identity.actorName + " · " + identity.teamName)
                    }
                }.textSelection(.enabled)
            }
            HStack {
                Button("Refresh status") { connection.refresh() }.disabled(connection.isBusy)
                Button(connection.connectorSaved ? "Review connector" : "Set up client…") { connection.previewConnector() }
                    .disabled(connection.isBusy || connection.sessionState != .active)
                    .buttonStyle(.borderedProminent)
                if connection.connectorSaved {
                    Button("Copy launch command") { connection.copyCommand() }.disabled(connection.isBusy)
                }
                Spacer()
                Button(connection.sessionState == .revocationPending ? "Retry sign out" : "Sign out") { connection.signOut() }
                    .disabled(connection.isBusy)
            }
            GroupBox("Context optimization") {
                VStack(alignment: .leading, spacing: 8) {
                    Toggle(
                        "Optimize eligible tool results on this Mac",
                        isOn: Binding(
                            get: { connection.contextOptimizationEnabled },
                            set: { connection.setContextOptimization(enabled: $0) }
                        )
                    )
                    .disabled(connection.isBusy || connection.sessionState != .active)
                    Text(connection.contextOptimizationStatusLabel)
                        .font(.callout)
                        .foregroundStyle(connection.contextOptimizationStatus == .settingsInvalid ? Color.orange : Color.secondary)
                    Text("The setting is Off by default and is checked for every request. New chats have the most stable cache behavior.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.top, 6)
            }
            if connection.isBusy { ProgressView().controlSize(.small) }
            if let dashboard = connection.dashboard {
                UsageView(dashboard: dashboard)
            } else {
                Text("Refresh status to verify this identity and read your usage. No model request is sent by this check.")
                    .font(.callout).foregroundStyle(.secondary)
            }
            if let expiry = connection.expiresAt {
                Text("Session ends \(expiry.formatted(date: .abbreviated, time: .shortened)). The client helper refreshes access within this limit.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Text("The launcher applies Hormuz settings only for that client session. Your normal client setup is unchanged. Client or managed settings can still affect behavior; this preview does not enforce device-wide routing.")
                .font(.caption).foregroundStyle(.secondary)
            Text("Connectors saved before context optimization must be reviewed and saved again once.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private func row(_ label: String, _ value: String) -> some View {
        GridRow { Text(label).foregroundStyle(.secondary); Text(value).lineLimit(2) }
    }
}
