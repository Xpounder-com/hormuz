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
            Text("Supported fixture versions: Codex 0.147.0 and Claude Code 2.1.233. Install the client separately. Start the launcher from your project directory; it accepts no override arguments. Moving Hormuz requires saving a new connector.")
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
