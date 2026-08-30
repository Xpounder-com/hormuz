import HormuzClientCore
import SwiftUI

struct ContentView: View {
    @Bindable var connection: ConnectionModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                HStack(alignment: .top) {
                    Image(systemName: "point.3.connected.trianglepath.dotted")
                        .font(.system(size: 34)).foregroundStyle(.tint).accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 5) {
                        Text("Your team's AI access").font(.title.bold())
                        Text("Connect the tools you use. Keep access governed.").foregroundStyle(.secondary)
                    }
                    Spacer()
                    Text("LOCAL PREVIEW").font(.caption2.weight(.semibold)).foregroundStyle(.secondary)
                }
                Label(connection.statusLabel, systemImage: connection.dashboard == nil ? "circle.dotted" : "checkmark.shield")
                    .font(.headline)
                    .foregroundStyle(connection.dashboard == nil ? Color.secondary : Color.green)
                    .accessibilityIdentifier("connection-status")
                if let message = connection.message {
                    Text(message).font(.callout).textSelection(.enabled)
                        .padding(12).frame(maxWidth: .infinity, alignment: .leading)
                        .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
                        .accessibilityIdentifier("connection-message")
                }
                if connection.hasSession {
                    ConnectedView(connection: connection)
                } else {
                    SignInView(connection: connection)
                }
                Divider()
                Text("Hormuz governs requests sent through its gateway. It is not a device VPN. Provider credentials stay with your team; this app stores only your revocable Hormuz session in Keychain.")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            .padding(28)
        }
        .sheet(isPresented: $connection.showingPreview) { ConnectorPreviewView(connection: connection) }
    }
}
