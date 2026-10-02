import HormuzClientCore
import SwiftUI

struct SignInView: View {
    @Bindable var connection: ConnectionModel
    @State private var advanced = false

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Sign in once. Hormuz will choose your organization and approved model.")
                .font(.callout).foregroundStyle(.secondary)
            if let message = connection.message {
                Text(message).font(.callout).textSelection(.enabled)
                    .accessibilityIdentifier("connection-message")
            }
            if connection.desktopSignInAvailable {
                Button("Continue with Hormuz", action: connection.signInDesktop)
                    .buttonStyle(DesktopPrimaryButtonStyle()).keyboardShortcut(.defaultAction)
                    .disabled(connection.isBusy)
                    .accessibilityIdentifier("desktop-sign-in-button")
                Button("Use team invitation", action: connection.joinTeamDesktop)
                    .disabled(connection.isBusy)
                    .accessibilityIdentifier("desktop-join-team-button")
            } else {
                Label("Hosted sign-in is not configured in this build.", systemImage: "info.circle")
                    .foregroundStyle(.secondary)
            }
            if connection.isBusy {
                HStack {
                    ProgressView().controlSize(.small)
                    Text(connection.awaitingBrowser ? "Finish signing in in your browser…" : "Connecting…")
                    if connection.awaitingBrowser { Button("Open sign-in page") { connection.reopenBrowser() } }
                    Button("Cancel") { connection.cancelSignIn() }
                }
            }
            DisclosureGroup("Advanced: self-hosted gateway", isExpanded: $advanced) {
                manualSetup.padding(.top, 12)
            }
        }
    }

    private var manualSetup: some View {
        VStack(alignment: .leading, spacing: 14) {
            Form {
                TextField("Gateway", text: $connection.gateway, prompt: Text("https://gateway.example.com"))
                    .accessibilityIdentifier("gateway-field")
                TextField("Organization ID", text: $connection.organization, prompt: Text("Provided by your team"))
                    .accessibilityIdentifier("organization-field")
                Picker("AI client", selection: $connection.client) {
                    ForEach(AIClient.allCases) { Text($0.title).tag($0) }
                }
                TextField("Model alias", text: $connection.model, prompt: Text("An alias approved by your team"))
                    .accessibilityIdentifier("model-field")
            }
            .disabled(connection.isBusy)
            TextField("OIDC issuer (optional)", text: $connection.issuer)
            Toggle("Allow HTTP for a local development gateway", isOn: $connection.allowLoopbackHTTP)
            Text("Loopback only. A hosted team gateway must use HTTPS.").font(.caption).foregroundStyle(.secondary)
            Text("You'll sign in on your team's identity page in your browser. Hormuz does not collect your password.")
                .font(.callout).foregroundStyle(.secondary)
            HStack {
                if !connection.isBusy {
                    Button("Sign in with browser") { connection.signIn() }
                        .buttonStyle(.bordered)
                        .disabled(connection.gateway.isEmpty || connection.organization.isEmpty || connection.model.isEmpty)
                        .accessibilityIdentifier("sign-in-button")
                }
            }
        }
    }
}

private struct DesktopPrimaryButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.headline)
            .foregroundStyle(.white)
            .padding(.horizontal, 18)
            .padding(.vertical, 10)
            .background(Color(hex: 0x397BFF).opacity(configuration.isPressed ? 0.7 : 1),
                        in: RoundedRectangle(cornerRadius: 9))
            .opacity(isEnabled ? 1 : 0.5)
    }
}
