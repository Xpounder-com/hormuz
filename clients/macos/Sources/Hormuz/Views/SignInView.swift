import HormuzClientCore
import SwiftUI

struct SignInView: View {
    @Bindable var connection: ConnectionModel
    @State private var advanced = false

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Form {
                Picker(
                    "Gateway setup",
                    selection: Binding(
                        get: { connection.setup },
                        set: { connection.selectSetup($0) }
                    )
                ) {
                    Text("Hormuz hosted pilot — Codex/OpenAI")
                        .tag(GatewaySetup.openAIPilot)
                    Text("Custom team gateway").tag(GatewaySetup.custom)
                }
                .accessibilityIdentifier("gateway-setup-picker")
                TextField("Gateway", text: $connection.gateway, prompt: Text("https://gateway.example.com"))
                    .accessibilityIdentifier("gateway-field")
                TextField("Organization ID", text: $connection.organization, prompt: Text("Provided by your team"))
                    .accessibilityIdentifier("organization-field")
                if connection.setup == .openAIPilot {
                    LabeledContent("AI client", value: "Codex")
                    Picker("Model alias", selection: $connection.model) {
                        Text("openai-primary").tag("openai-primary")
                        Text("openai-secondary").tag("openai-secondary")
                    }
                    .accessibilityIdentifier("model-alias-picker")
                    Text("This pilot connects Codex to your team's OpenAI gateway. Model fallback stays within OpenAI. Claude Code and protection from an OpenAI-wide outage are not included.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    Picker("AI client", selection: $connection.client) {
                        ForEach(AIClient.allCases) { Text($0.title).tag($0) }
                    }
                    .accessibilityIdentifier("client-picker")
                    TextField("Model alias", text: $connection.model, prompt: Text("An alias approved by your team"))
                        .accessibilityIdentifier("model-field")
                    Text("Use the client and model alias approved by your gateway administrator.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .disabled(connection.isBusy)
            DisclosureGroup("Advanced connection settings", isExpanded: $advanced) {
                VStack(alignment: .leading, spacing: 10) {
                    TextField("OIDC issuer (optional)", text: $connection.issuer)
                    if connection.setup == .custom {
                        Toggle("Allow HTTP for a local development gateway", isOn: $connection.allowLoopbackHTTP)
                        Text("Loopback only. A hosted team gateway must use HTTPS.").font(.caption).foregroundStyle(.secondary)
                    }
                }.padding(.top, 10).disabled(connection.isBusy)
            }
            Text("You'll sign in on your team's identity page in your browser. Hormuz does not collect your password.")
                .font(.callout).foregroundStyle(.secondary)
            HStack {
                if connection.isBusy {
                    ProgressView().controlSize(.small)
                    Text(connection.awaitingBrowser ? "Finish signing in in your browser…" : "Connecting…")
                    Spacer()
                    if connection.awaitingBrowser { Button("Open sign-in page") { connection.reopenBrowser() } }
                    Button("Cancel") { connection.cancelSignIn() }
                } else {
                    Button("Sign in with browser") { connection.signIn() }
                        .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                        .disabled(connection.gateway.isEmpty || connection.organization.isEmpty || connection.model.isEmpty)
                        .accessibilityIdentifier("sign-in-button")
                }
            }
        }
    }
}
