import HormuzClientCore
import SwiftUI

struct ControlCenterView: View {
    @Bindable var connection: ConnectionModel
    @ObservedObject var presentation: CompanionPresentationModel
    let displayOptions: [DisplayOption]
    let showWidget: () -> Void

    var body: some View {
        TabView {
            ContentView(connection: connection)
                .tabItem { Label("Connection", systemImage: "link") }

            CompanionPreferencesView(
                presentation: presentation,
                displayOptions: displayOptions,
                showWidget: showWidget
            )
            .tabItem { Label("Appearance", systemImage: "circle.lefthalf.filled") }
        }
        .frame(minWidth: 650, minHeight: 650)
    }
}

private struct CompanionPreferencesView: View {
    @ObservedObject var presentation: CompanionPresentationModel
    let displayOptions: [DisplayOption]
    let showWidget: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 24) {
            VStack(alignment: .leading, spacing: 6) {
                Text("Desktop companion")
                    .font(.title.bold())
                Text("Keep verified Hormuz status and gateway usage at the edge of your screen.")
                    .foregroundStyle(.secondary)
            }

            GroupBox("Widget") {
                Form {
                    Picker("Visibility", selection: visibilityBinding) {
                        ForEach(CompanionVisibilityMode.allCases) { mode in
                            Text(mode.displayName).tag(mode)
                        }
                    }

                    Picker("UI scale", selection: scaleBinding) {
                        Text("100%").tag(1.0)
                        Text("125%").tag(1.25)
                        Text("150%").tag(1.5)
                    }

                    Picker("Display", selection: displayBinding) {
                        Text("Primary display").tag("primary")
                        ForEach(displayOptions) { option in
                            Text(option.name).tag(option.id)
                        }
                    }
                }
                .formStyle(.grouped)
                .padding(.top, 8)
            }

            HStack {
                Button("Show widget", action: showWidget)
                    .buttonStyle(.borderedProminent)
                Spacer()
            }

            Text("The rings show the exact cost, token, and request totals reported by the gateway. Hormuz does not display a percentage until the gateway supplies a verified limit.")
                .font(.callout)
                .foregroundStyle(.secondary)

            Spacer()
        }
        .padding(28)
    }

    private var visibilityBinding: Binding<CompanionVisibilityMode> {
        Binding(get: { presentation.visibilityMode }, set: presentation.setVisibilityMode)
    }

    private var scaleBinding: Binding<Double> {
        Binding(get: { presentation.uiScale }, set: presentation.setUIScale)
    }

    private var displayBinding: Binding<String> {
        Binding(get: { presentation.selectedDisplayID }, set: presentation.setSelectedDisplayID)
    }
}
