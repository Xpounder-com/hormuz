import HormuzClientCore
import SwiftUI

struct RemovalConfirmationView: View {
    @Bindable var connection: ConnectionModel
    let resetAppearance: () -> Void
    @State private var resetAppearanceSelected = false

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(connection.removalResult == nil ? "Remove local setup" : "Removal result")
                .font(.title2.bold())
            if let result = connection.removalResult {
                if result.keychain_session_absent && result.generated_setup_absent && !result.pending {
                    Label("Local setup removed; native session absent", systemImage: "checkmark.shield")
                    Text("Removed \(result.removed_files) verified setup files. Retained \(result.retained_files) edited, shared or unowned entries and empty coordination locks.")
                    Text("To remove the app, quit and move the revealed Hormuz app to Trash in Finder. Your team gateway, billing subscription and ordinary agent settings remain separate.")
                    Button("Quit and show app in Finder", action: connection.quitAndRevealApp)
                        .buttonStyle(.borderedProminent)
                } else {
                    Label("Removal has not been verified complete", systemImage: "exclamationmark.circle")
                }
                Button("Done") { connection.showingRemoval = false }
            } else if let preview = connection.removalPreview {
                Text("All native Hormuz copies for this macOS user share one connection. This stops clients launched by this app, revokes that connection and removes its verified generated setup.")
                Text("\(preview.generated_files) verified setup files selected. \(preview.retained_files) edited, shared or unowned entries will be kept. Backups and ordinary agent settings are preserved.")
                if preview.pending {
                    Label("An interrupted removal will resume. Local use remains disabled until it finishes.", systemImage: "exclamationmark.circle")
                }
                Toggle("Reset Hormuz appearance settings", isOn: $resetAppearanceSelected)
                Text("Close other Hormuz copies and their clients first. Moving the app to Trash later does not cancel a paid subscription.")
                    .font(.callout).foregroundStyle(.secondary)
                HStack {
                    Button("Cancel") { connection.showingRemoval = false }
                    Spacer()
                    Button(preview.pending ? "Retry removal" : "Remove local setup", role: .destructive) {
                        connection.applyRemoval(resetAppearanceSelected: resetAppearanceSelected, resetAppearance: resetAppearance)
                    }.buttonStyle(.borderedProminent)
                }.disabled(connection.isBusy)
            }
            if connection.isBusy { ProgressView("Removing local setup…") }
            if let message = connection.message { Text(message).font(.callout).textSelection(.enabled) }
        }
        .padding(24).frame(width: 510)
        .interactiveDismissDisabled(connection.isBusy)
        .accessibilityIdentifier("native-removal-confirmation")
    }
}
