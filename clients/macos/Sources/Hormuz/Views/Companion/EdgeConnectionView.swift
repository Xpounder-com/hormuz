import HormuzClientCore
import SwiftUI

struct EdgeConnectionView: View {
    @Bindable var connection: ConnectionModel
    @ObservedObject var navigation: EdgeHubNavigation
    let scale: CGFloat
    let showSetup: Bool
    @State private var advanced = false

    private var needsReconnect: Bool {
        connection.sessionState == .refreshPending || (connection.expiresAt.map { $0 <= Date() } ?? false)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16 * scale) {
            if connection.awaitingBrowser {
                Label("Finish signing in in your browser", systemImage: "globe")
                Text("You can leave this card open or come back when you’re done.").foregroundStyle(.secondary)
                Button("Open sign-in page", action: connection.reopenBrowser)
                Button("Cancel sign-in", action: connection.cancelSignIn)
            } else if connection.hasSession {
                session
            } else if showSetup {
                setup
            } else {
                Text("Connect with your team’s gateway and organization details.").foregroundStyle(.secondary)
                Button("Connect to Hormuz") { navigation.open(.setup) }
                    .buttonStyle(EdgePrimaryButtonStyle(scale: scale))
            }
            if connection.isBusy {
                HStack { ProgressView().controlSize(.small); Text("Working…").foregroundStyle(.secondary) }
            }
        }
        .textFieldStyle(.roundedBorder)
        .buttonStyle(.bordered)
    }

    private var session: some View {
        VStack(alignment: .leading, spacing: 16 * scale) {
            Label(connection.statusLabel, systemImage: needsReconnect || connection.sessionState == .revocationPending
                  ? "exclamationmark.circle" : "checkmark.shield")
                .foregroundStyle(needsReconnect || connection.sessionState == .revocationPending ? Color.orange : Color.primary)
            if let identity = connection.dashboard?.identity {
                detail("Signed in as", "\(identity.actorName) · \(identity.teamName)")
            }
            detail("Organization", connection.profile?.organization ?? connection.organization)
            detail("Gateway", connection.profile?.gateway ?? connection.gateway)
            if let expiry = connection.expiresAt {
                detail("Session ends", expiry.formatted(date: .abbreviated, time: .shortened))
            }
            Label("Session stored securely in Keychain", systemImage: "lock.shield")
                .font(.system(size: 11 * scale)).foregroundStyle(.secondary)
            Text("Access refreshes within this session’s lifetime. Provider keys stay with your team.")
                .font(.system(size: 11 * scale)).foregroundStyle(.secondary)
            if connection.sessionState == .revocationPending {
                Button("Retry sign out", action: connection.signOut).disabled(connection.isBusy)
            } else {
                Button(needsReconnect ? "Reconnect" : "Refresh status") {
                    needsReconnect ? connection.reconnect() : connection.refresh()
                }
                .buttonStyle(EdgePrimaryButtonStyle(scale: scale)).disabled(connection.isBusy)
                Button("Sign out", action: connection.signOut).disabled(connection.isBusy)
            }
        }
    }

    private var setup: some View {
        VStack(alignment: .leading, spacing: 14 * scale) {
            Text("Use the details provided by your team.").foregroundStyle(.secondary)
            field("Gateway", text: $connection.gateway, prompt: "https://gateway.example.com")
            field("Organization", text: $connection.organization, prompt: "Organization ID")
            VStack(alignment: .leading, spacing: 6 * scale) {
                Text("AI client").foregroundStyle(.secondary)
                Picker("AI client", selection: $connection.client) {
                    ForEach(AIClient.allCases) { Text($0.title).tag($0) }
                }.labelsHidden().pickerStyle(.menu)
            }
            field("Model alias", text: $connection.model, prompt: "Approved by your team")
            DisclosureGroup("Advanced settings", isExpanded: $advanced) {
                VStack(alignment: .leading, spacing: 12 * scale) {
                    field("OIDC issuer", text: $connection.issuer, prompt: "Optional")
                    Toggle("Allow HTTP for local development", isOn: $connection.allowLoopbackHTTP)
                    Text("Loopback only. Team gateways require HTTPS.").foregroundStyle(.secondary)
                }.padding(.top, 10 * scale)
            }
            Text("Sign in securely in your browser. Hormuz never collects your password.")
                .font(.system(size: 11 * scale)).foregroundStyle(.secondary)
            Button("Sign in with browser", action: connection.signIn)
                .buttonStyle(EdgePrimaryButtonStyle(scale: scale))
                .disabled(connection.gateway.isEmpty || connection.organization.isEmpty || connection.model.isEmpty)
        }.disabled(connection.isBusy)
    }

    private func field(_ title: String, text: Binding<String>, prompt: String) -> some View {
        VStack(alignment: .leading, spacing: 6 * scale) {
            Text(title).foregroundStyle(.secondary)
            TextField(title, text: text, prompt: Text(prompt)).labelsHidden()
        }
    }

    private func detail(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 5 * scale) {
            Text(label).font(.system(size: 11 * scale)).foregroundStyle(.secondary)
            Text(value).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
        }
    }
}
