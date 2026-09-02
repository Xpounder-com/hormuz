import HormuzClientCore
import SwiftUI

struct SignInView: View {
    @Bindable var connection: ConnectionModel
    @State private var advanced = false

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
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
            DisclosureGroup("Advanced connection settings", isExpanded: $advanced) {
                VStack(alignment: .leading, spacing: 10) {
                    TextField("OIDC issuer (optional)", text: $connection.issuer)
                    Toggle("Allow HTTP for a local development gateway", isOn: $connection.allowLoopbackHTTP)
                    Text("Loopback only. A hosted team gateway must use HTTPS.").font(.caption).foregroundStyle(.secondary)
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
