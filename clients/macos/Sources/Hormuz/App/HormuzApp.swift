import SwiftUI

struct HormuzApp: App {
    @State private var connection = ConnectionModel()

    var body: some Scene {
        WindowGroup("Hormuz", id: "connection") {
            ContentView(connection: connection)
                .frame(minWidth: 650, minHeight: 650)
                .task { await connection.restore() }
        }
        .defaultSize(width: 720, height: 760)
        .commands {
            CommandGroup(replacing: .newItem) { }
            CommandMenu("Connection") {
                Button("Refresh status") { connection.refresh() }
                    .keyboardShortcut("r", modifiers: .command)
                    .disabled(!connection.hasSession || connection.isBusy)
                Button("Sign out") { connection.signOut() }
                    .disabled(!connection.hasSession || connection.isBusy)
            }
        }
    }
}
