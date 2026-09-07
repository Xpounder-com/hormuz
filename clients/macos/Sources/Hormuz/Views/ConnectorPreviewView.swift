import SwiftUI

struct ConnectorPreviewView: View {
    @Bindable var connection: ConnectionModel
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Review client setup").font(.title2.bold())
            Text("These files belong to Hormuz. Existing Codex and Claude Code settings, login files, prompts, and history will not be changed.")
                .foregroundStyle(.secondary)
            ScrollView([.vertical, .horizontal]) {
                Text(connection.connector?.previewText ?? "").font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled).padding(12).frame(maxWidth: .infinity, alignment: .leading)
            }.background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
            if let profile = connection.profile {
                if profile.setup == .openAIPilot {
                    Text("Hosted pilot scope: Codex 0.147.0 through your team's OpenAI gateway. Fallback stays within OpenAI; this connector does not qualify Claude Code or cross-provider availability.")
                        .font(.caption).foregroundStyle(.secondary)
                } else {
                    Text("Custom gateway connector: \(profile.client.title) \(profile.client.testedVersion) is the tested fixture version for this integration. This app does not claim that this setup passed the hosted pilot.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            Text("Install the selected client separately. Start the launcher from your project directory; it accepts no override arguments. Moving Hormuz requires saving a new connector.")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                Button("Cancel") { connection.showingPreview = false }.keyboardShortcut(.cancelAction)
                Spacer()
                Button("Save connector") { connection.saveConnector() }.buttonStyle(.borderedProminent)
                    .disabled(connection.isBusy).keyboardShortcut(.defaultAction)
            }
        }.padding(24).frame(width: 680, height: 560)
    }
}
